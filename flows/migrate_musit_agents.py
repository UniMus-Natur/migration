"""Prefect flow: migrate MUSIT collection agents (ACTOR + PERSON_NAME) to Specify 7 Agent.

Source (Phase 1.1 — canonical MUSIT layer)
    MUSIT_BOTANIKK_FELLES.ACTOR + PERSON_NAME
    MUSIT_ZOOLOGI_ENTOMOLOGI.ACTOR + PERSON_NAME

Target (via Specify Django ORM)
    agent — collectors, determiners, orgs; no SpecifyUser link

This is separate from ``migrate_users`` (Phase 1.4), which creates login accounts from
``USD_METADATA.BRUKARAR``. The same human may exist in both ACTOR and BRUKARAR; deduplication
into a single Specify Agent is not implemented here — see docs/migrate_musit_agents.md.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_MUSIT_COLLECTION_AGENTS,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.musit_agent_names import (
    choose_agent_name_parts,
    preferred_person_name_id_sql,
)
from flows.lib.musit_agents_common import (
    ALLOWED_MUSIT_AGENT_SCHEMAS,
    find_agents_for_musit_actor,
    musit_actor_remarks_marker,
)
from flows.lib.oracle_connectivity import (
    create_oracle_connection,
    get_oracle_config_from_env,
)
from flows.lib.specify_setup import setup_django

_ALLOWED_SCHEMAS = ALLOWED_MUSIT_AGENT_SCHEMAS


def _remarks_marker(schema: str, actor_id: int) -> str:
    return musit_actor_remarks_marker(schema, actor_id)


def _trunc(s: str | None, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    return t if len(t) <= max_len else t[:max_len]


def musit_actor_type_to_specify_agent_type(actor_type: int | None) -> int:
    """Map MUSIT ACTOR_TYPE to Specify Agent.agenttype.

    MUSIT (per migration_strategy): 0=person, 1=organisation, 2=group.
    Specify: 1=Person, 0=Organization (see existing tests and migrate_users).
    """
    if actor_type is None:
        return 1
    if actor_type == 0:
        return 1
    if actor_type in (1, 2):
        return 0
    return 1


@dataclass
class MusitActorRow:
    schema: str
    actor_id: int
    actor_type: int | None
    actorname: str | None
    birthdate: date | datetime | None
    deathdate: date | datetime | None
    email_address: str | None
    institution: str | None
    note: str | None
    person_given_name: str | None
    person_surname: str | None
    person_middle_name: str | None
    title: str | None


@dataclass
class MusitAgentMigrationResult:
    agents_created: int = 0
    agents_skipped: int = 0
    agents_updated: int = 0
    agents_unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    schemas_processed: list[str] = field(default_factory=list)


# Cap error strings persisted (avoid huge task results / reports).
_MAX_ERROR_LINES = 200

# Dry-run / live progress INFO every N rows (per-row detail is DEBUG only).
_PROGRESS_LOG_EVERY = 5000


@dataclass
class MusitAgentRunOutcome:
    """Small return value from the combined extract+load task (no giant row list)."""

    result: MusitAgentMigrationResult
    oracle_actors_extracted: int
    oracle_rows_per_schema: dict[str, int]
    oracle_actor_type_counts: dict[str, int]


def _sql_for_schema(schema: str) -> str:
    if schema not in _ALLOWED_SCHEMAS:
        raise ValueError(f"Unsupported MUSIT schema: {schema}")
    # One row per ACTOR: PERSON_NAME matching ACTORNAME (else fullest) for title /
    # fallback parts. Agent primary name prefers ACTORNAME in Python (issue #74).
    pref = preferred_person_name_id_sql(schema)
    return f"""
        SELECT
            a.ACTOR_ID,
            a.ACTOR_TYPE,
            a.ACTORNAME,
            a.BIRTHDATE,
            a.DEATHDATE,
            a.EMAIL_ADDRESS,
            a.INSTITUTION,
            a.NOTE,
            pn.PERSON_GIVEN_NAME,
            pn.PERSON_SURNAME,
            pn.PERSON_MIDDLE_NAME,
            pn.TITLE
        FROM {schema}.ACTOR a
        LEFT JOIN {schema}.PERSON_NAME pn
          ON pn.PERSON_NAME_ID = {pref}
        ORDER BY a.ACTOR_ID
    """


def _row_from_raw(schema: str, cols: list[str], raw: tuple) -> MusitActorRow:
    row = dict(zip(cols, raw))
    return MusitActorRow(
        schema=schema,
        actor_id=int(row["actor_id"]),
        actor_type=int(row["actor_type"]) if row.get("actor_type") is not None else None,
        actorname=row.get("actorname"),
        birthdate=row.get("birthdate"),
        deathdate=row.get("deathdate"),
        email_address=row.get("email_address"),
        institution=row.get("institution"),
        note=row.get("note"),
        person_given_name=row.get("person_given_name"),
        person_surname=row.get("person_surname"),
        person_middle_name=row.get("person_middle_name"),
        title=row.get("title"),
    )


def _desired_name_fields(row: MusitActorRow, agent_type: int) -> dict[str, str | None]:
    parts = choose_agent_name_parts(
        person_given_name=row.person_given_name,
        person_surname=row.person_surname,
        person_middle_name=row.person_middle_name,
        title=row.title,
        actorname=row.actorname,
        agent_type=agent_type,
    )
    return {
        "firstname": parts.firstname,
        "lastname": parts.lastname,
        "middleinitial": parts.middleinitial,
        "title": parts.title,
    }


def _name_field_updates(agent, desired: dict[str, str | None]) -> dict[str, str | None]:
    """Return Agent name columns that differ from ``desired``."""
    changed: dict[str, str | None] = {}
    for field, want in desired.items():
        have = getattr(agent, field, None)
        have_n = (str(have).strip() if have is not None else "") or None
        want_n = (str(want).strip() if want is not None else "") or None
        if have_n != want_n:
            changed[field] = want
    return changed


def _process_one_musit_actor_row(
    row: MusitActorRow,
    *,
    division,
    dry_run: bool,
    update_existing_names: bool,
    logger,
    result: MusitAgentMigrationResult,
    processed_count: int,
) -> None:
    from specifyweb.specify.models import Agent

    marker = _remarks_marker(row.schema, row.actor_id)
    agent_type = musit_actor_type_to_specify_agent_type(row.actor_type)
    desired_names = _desired_name_fields(row, agent_type)
    first = desired_names["firstname"]
    last = desired_names["lastname"]
    middle = desired_names["middleinitial"]
    title = desired_names["title"]

    # Match marker exactly or with trailing extras (institution / oracle_note).
    # Does not match oracle_note-only orphans — those need the cleanup flow.
    existing_agents = find_agents_for_musit_actor(row.schema, row.actor_id)
    if existing_agents:
        if not update_existing_names:
            result.agents_skipped += 1
            return
        any_changed = False
        for existing in existing_agents:
            changes = _name_field_updates(existing, desired_names)
            if not changes:
                continue
            any_changed = True
            if dry_run:
                logger.debug(
                    f"[DRY RUN] Would update Agent id={existing.id} "
                    f"schema={row.schema} ACTOR_ID={row.actor_id} fields={changes}"
                )
                continue
            try:
                for field, value in changes.items():
                    setattr(existing, field, value)
                existing.timestampmodified = datetime.now(timezone.utc)
                existing.save(update_fields=[*changes.keys(), "timestampmodified"])
            except Exception as exc:
                msg = (
                    f"Error updating {row.schema}.ACTOR_ID={row.actor_id} "
                    f"(agent_id={existing.id}): {exc}"
                )
                logger.error(msg)
                if len(result.errors) < _MAX_ERROR_LINES:
                    result.errors.append(msg)
                elif len(result.errors) == _MAX_ERROR_LINES:
                    result.errors.append(
                        f"... further errors omitted (cap {_MAX_ERROR_LINES}); "
                        "see worker logs"
                    )
        if any_changed:
            result.agents_updated += 1
        else:
            result.agents_unchanged += 1
        if (
            not dry_run
            and any_changed
            and processed_count % _PROGRESS_LOG_EVERY == 0
        ):
            logger.info(
                f"Live progress: {processed_count} actors processed "
                f"(created {result.agents_created}, updated {result.agents_updated}, "
                f"unchanged {result.agents_unchanged}, skipped {result.agents_skipped})"
            )
        return

    email = _trunc(row.email_address, 50)

    remarks_parts = [marker]
    if row.institution:
        remarks_parts.append(f"institution={_trunc(row.institution, 200)}")
    if row.note:
        remarks_parts.append(f"oracle_note={_trunc(row.note, 500)}")
    remarks = "; ".join(remarks_parts)

    dob = row.birthdate
    dod = row.deathdate
    if isinstance(dob, datetime):
        dob = dob.date()
    if isinstance(dod, datetime):
        dod = dod.date()

    if dry_run:
        logger.debug(
            f"[DRY RUN] Would create Agent schema={row.schema} ACTOR_ID={row.actor_id} "
            f"type={agent_type} name={first or ''} {last}"
        )
        result.agents_created += 1
        if processed_count % _PROGRESS_LOG_EVERY == 0:
            logger.info(
                f"[DRY RUN] progress: {processed_count} actors processed "
                f"(would create {result.agents_created}, skip {result.agents_skipped})"
            )
        return

    try:
        agent = Agent(
            agenttype=agent_type,
            firstname=first,
            lastname=last or "(unknown)",
            middleinitial=middle,
            title=title,
            email=email,
            dateofbirth=dob,
            dateofdeath=dod,
            division=division,
            specifyuser=None,
            remarks=remarks,
        )
        agent.save()
        result.agents_created += 1
        if processed_count % _PROGRESS_LOG_EVERY == 0:
            logger.info(
                f"Live progress: {processed_count} actors processed "
                f"(created {result.agents_created}, updated {result.agents_updated}, "
                f"skipped {result.agents_skipped})"
            )
    except Exception as exc:
        msg = f"Error migrating {row.schema}.ACTOR_ID={row.actor_id}: {exc}"
        logger.error(msg)
        if len(result.errors) < _MAX_ERROR_LINES:
            result.errors.append(msg)
        elif len(result.errors) == _MAX_ERROR_LINES:
            result.errors.append(
                f"... further errors omitted (cap {_MAX_ERROR_LINES}); see worker logs"
            )


@task(retries=1, retry_delay_seconds=5)
def extract_and_load_musit_agents_task(
    oracle_env: str,
    schemas: list[str],
    dry_run: bool = True,
    update_existing_names: bool = False,
) -> MusitAgentRunOutcome:
    """Extract ACTOR rows from Oracle and load into Specify in one task.

    Avoids returning ~250k ``MusitActorRow`` objects across a Prefect task boundary
    (large result serialization and memory). Logs at INFO every
    ``_PROGRESS_LOG_EVERY`` rows instead of per row.
    """
    setup_django()
    logger = get_run_logger()
    from specifyweb.specify.models import Division

    division = Division.objects.first()
    if division is None:
        raise RuntimeError("No Division found in Specify — the database must be initialized first")

    for s in schemas:
        if s not in _ALLOWED_SCHEMAS:
            raise ValueError(f"Unsupported schema: {s}")

    result = MusitAgentMigrationResult()
    type_counts: Counter[int | None] = Counter()
    rows_per_schema: Counter[str] = Counter()
    total = 0

    config = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config)
    try:
        for schema in schemas:
            sql = _sql_for_schema(schema)
            with connection.cursor() as cur:
                cur.execute(sql)
                cols = [d[0].lower() for d in cur.description]
                schema_count = 0
                for raw in cur:
                    total += 1
                    schema_count += 1
                    row = _row_from_raw(schema, cols, raw)
                    type_counts[row.actor_type] += 1
                    rows_per_schema[row.schema] += 1
                    _process_one_musit_actor_row(
                        row,
                        division=division,
                        dry_run=dry_run,
                        update_existing_names=update_existing_names,
                        logger=logger,
                        result=result,
                        processed_count=total,
                    )
            logger.info(f"Finished scanning {schema}.ACTOR ({schema_count} rows)")
    finally:
        connection.close()

    result.schemas_processed = sorted(schemas)

    actor_type_ordered: dict[str, int] = {}
    for k in sorted(type_counts.keys(), key=lambda x: (x is None, x if x is not None else 0)):
        key = "null" if k is None else str(k)
        actor_type_ordered[key] = type_counts[k]

    logger.info(
        f"MUSIT agent extract+load task done: total_oracle_rows={total}, "
        f"agents_created={result.agents_created}, updated={result.agents_updated}, "
        f"unchanged={result.agents_unchanged}, skipped={result.agents_skipped}, "
        f"errors={len(result.errors)}"
    )

    return MusitAgentRunOutcome(
        result=result,
        oracle_actors_extracted=total,
        oracle_rows_per_schema=dict(sorted(rows_per_schema.items())),
        oracle_actor_type_counts=actor_type_ordered,
    )


def _musit_agent_report_dict(
    ts: str,
    oracle_env: str,
    dry_run: bool,
    musit_schemas: list[str],
    outcome: MusitAgentRunOutcome,
) -> dict:
    r = outcome.result
    return {
        "report_version": 1,
        "flow": "migrate_musit_agents",
        "migration_phase": "1.1",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "musit_schemas": list(musit_schemas),
        "oracle_actors_extracted": outcome.oracle_actors_extracted,
        "oracle_rows_per_schema": outcome.oracle_rows_per_schema,
        "oracle_actor_type_counts": outcome.oracle_actor_type_counts,
        "agents_created": r.agents_created,
        "agents_skipped": r.agents_skipped,
        "agents_updated": r.agents_updated,
        "agents_unchanged": r.agents_unchanged,
        "agents_linked": 0,
        "schemas_processed": r.schemas_processed,
        "errors": r.errors,
    }


@flow(
    name="Migrate MUSIT Actors",
    description=(
        "Phase 1.1: Load MUSIT ACTOR + PERSON_NAME (botany and/or entomology) "
        "into Specify Agent records for specimen linking."
    ),
)
def migrate_musit_agents_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    musit_schemas: list[str] | None = None,
    update_existing_names: bool = False,
) -> dict:
    """Migrate MUSIT ACTOR rows to Specify ``Agent`` (no logins).

    Args:
        oracle_env: Oracle environment prefix (e.g. PROD, TEST).
        dry_run: When True, log only; no ``Agent`` rows are written.
        musit_schemas: Subset of ``MUSIT_BOTANIKK_FELLES`` and/or ``MUSIT_ZOOLOGI_ENTOMOLOGI``.
            Defaults to both when omitted (``None``).
        update_existing_names: When True, existing Agents (matched by remarks marker,
            including trailing ``institution`` / ``oracle_note`` extras) get
            ``firstname``/``lastname``/``middleinitial``/``title`` refreshed from
            ``ACTORNAME`` (else ``PERSON_NAME``) — use this to fix abbreviated
            display names in staging without recreating Agents or remigrating
            specimens.
    """
    logger = get_run_logger()
    if musit_schemas is None:
        musit_schemas = ["MUSIT_BOTANIKK_FELLES", "MUSIT_ZOOLOGI_ENTOMOLOGI"]

    for s in musit_schemas:
        if s not in _ALLOWED_SCHEMAS:
            raise ValueError(
                f"Invalid musit_schemas entry {s!r}; "
                f"allowed: {sorted(_ALLOWED_SCHEMAS)}"
            )

    logger.info(
        f"Starting MUSIT agent migration (oracle_env={oracle_env}, dry_run={dry_run}, "
        f"update_existing_names={update_existing_names}, schemas={musit_schemas})"
    )

    setup_django()

    outcome = extract_and_load_musit_agents_task(
        oracle_env,
        list(musit_schemas),
        dry_run,
        update_existing_names,
    )
    result = outcome.result

    logger.info(
        f"MUSIT agent migration complete: created={result.agents_created}, "
        f"updated={result.agents_updated}, unchanged={result.agents_unchanged}, "
        f"skipped={result.agents_skipped}, errors={len(result.errors)}"
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = _musit_agent_report_dict(ts, oracle_env, dry_run, musit_schemas, outcome)
    report["update_existing_names"] = update_existing_names
    s3_key = migration_report_s3_key(REPORT_CATEGORY_MUSIT_COLLECTION_AGENTS, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info(f"Uploaded report: {uri}")

    return {
        "agents_created": result.agents_created,
        "agents_updated": result.agents_updated,
        "agents_unchanged": result.agents_unchanged,
        "agents_skipped": result.agents_skipped,
        "oracle_actors_extracted": outcome.oracle_actors_extracted,
        "errors": result.errors,
        "schemas_processed": result.schemas_processed,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
    }
