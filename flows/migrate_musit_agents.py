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

import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    SPECIFY7_COLLECTION_AGENTS_ACTOR,
    migration_report_s3_key,
)
from flows.lib.oracle_connectivity import (
    create_oracle_connection,
    get_oracle_config_from_env,
)
from flows.lib.s3_connectivity import upload_file_with_compat_retry
from flows.lib.specify_setup import setup_django

# Only schemas present in our Oracle inventory and used for specimen events.
_ALLOWED_SCHEMAS = frozenset({
    "MUSIT_BOTANIKK_FELLES",
    "MUSIT_ZOOLOGI_ENTOMOLOGI",
})


def _remarks_marker(schema: str, actor_id: int) -> str:
    return f"MUSIT-migration: ACTOR; schema={schema}; ACTOR_ID={actor_id}"


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
    errors: list[str] = field(default_factory=list)
    schemas_processed: list[str] = field(default_factory=list)


def _sql_for_schema(schema: str) -> str:
    if schema not in _ALLOWED_SCHEMAS:
        raise ValueError(f"Unsupported MUSIT schema: {schema}")
    # One row per ACTOR: prefer VALID_PERSON_NAME_ID, else lexicographically smallest PERSON_NAME_ID.
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
          ON pn.PERSON_NAME_ID = NVL(
            a.VALID_PERSON_NAME_ID,
            (SELECT MIN(pn2.PERSON_NAME_ID)
             FROM {schema}.PERSON_NAME pn2
             WHERE pn2.ACTOR_ID = a.ACTOR_ID)
          )
        ORDER BY a.ACTOR_ID
    """


@task(retries=2, retry_delay_seconds=5)
def extract_musit_actors(oracle_env: str, schemas: list[str]) -> list[MusitActorRow]:
    logger = get_run_logger()
    config = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config)
    out: list[MusitActorRow] = []
    try:
        for schema in schemas:
            if schema not in _ALLOWED_SCHEMAS:
                raise ValueError(f"Unsupported schema: {schema}")
            sql = _sql_for_schema(schema)
            with connection.cursor() as cur:
                cur.execute(sql)
                cols = [d[0].lower() for d in cur.description]
                for raw in cur.fetchall():
                    row = dict(zip(cols, raw))
                    out.append(
                        MusitActorRow(
                            schema=schema,
                            actor_id=int(row["actor_id"]),
                            actor_type=int(row["actor_type"])
                            if row.get("actor_type") is not None
                            else None,
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
                    )
            logger.info(f"Fetched {sum(1 for r in out if r.schema == schema)} rows from {schema}.ACTOR")
    finally:
        connection.close()
    logger.info(f"Total MUSIT ACTOR rows extracted: {len(out)}")
    return out


@task(retries=1, retry_delay_seconds=3)
def load_musit_actors_to_specify(
    rows: list[MusitActorRow],
    dry_run: bool = True,
) -> MusitAgentMigrationResult:
    logger = get_run_logger()
    from specifyweb.specify.models import Agent, Division

    division = Division.objects.first()
    if division is None:
        raise RuntimeError("No Division found in Specify — the database must be initialized first")

    result = MusitAgentMigrationResult()
    seen_schemas: set[str] = set()

    for row in rows:
        seen_schemas.add(row.schema)
        marker = _remarks_marker(row.schema, row.actor_id)
        existing = Agent.objects.filter(remarks=marker).first()
        if existing is not None:
            result.agents_skipped += 1
            continue

        agent_type = musit_actor_type_to_specify_agent_type(row.actor_type)
        first = _trunc(row.person_given_name, 50)
        last = _trunc(row.person_surname, 256)
        if agent_type == 1 and not (first or last):
            last = _trunc(row.actorname, 256) or "(unknown)"
        if agent_type == 0 and not last:
            last = _trunc(row.actorname, 256) or "(organization)"

        email = _trunc(row.email_address, 50)
        title = _trunc(row.title, 50)
        middle = _trunc(row.person_middle_name, 50)

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
            logger.info(
                f"[DRY RUN] Would create Agent schema={row.schema} ACTOR_ID={row.actor_id} "
                f"type={agent_type} name={first or ''} {last}"
            )
            result.agents_created += 1
            continue

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
            logger.info(f"Created Agent id={agent.id} for {row.schema}.ACTOR_ID={row.actor_id}")
        except Exception as exc:
            msg = f"Error migrating {row.schema}.ACTOR_ID={row.actor_id}: {exc}"
            logger.error(msg)
            result.errors.append(msg)

    result.schemas_processed = sorted(seen_schemas)
    return result


def _actor_type_counts(rows: list[MusitActorRow]) -> dict[str, int]:
    c = Counter(r.actor_type for r in rows)
    ordered: dict[str, int] = {}
    for k in sorted(c.keys(), key=lambda x: (x is None, x if x is not None else 0)):
        key = "null" if k is None else str(k)
        ordered[key] = c[k]
    return ordered


def _rows_per_schema(rows: list[MusitActorRow]) -> dict[str, int]:
    c = Counter(r.schema for r in rows)
    return dict(sorted(c.items()))


def _upload_report(
    result: MusitAgentMigrationResult,
    oracle_env: str,
    dry_run: bool,
    musit_schemas: list[str],
    oracle_rows: list[MusitActorRow],
) -> list[str]:
    log = get_run_logger()
    bucket = (os.getenv("S3_BUCKET") or "").strip()
    if not bucket:
        log.warning(
            "S3_BUCKET is not set (or empty): skipping MUSIT agent migration report upload. "
            "Set S3_BUCKET and S3 credentials on the process running this flow."
        )
        return []

    prefix = os.getenv("S3_PREFIX", "oracle-schema").strip("/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = migration_report_s3_key(prefix, SPECIFY7_COLLECTION_AGENTS_ACTOR, ts)

    report = {
        "report_version": 1,
        "flow": "migrate_musit_agents",
        "migration_phase": "1.1",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "musit_schemas": list(musit_schemas),
        "oracle_actors_extracted": len(oracle_rows),
        "oracle_rows_per_schema": _rows_per_schema(oracle_rows),
        "oracle_actor_type_counts": _actor_type_counts(oracle_rows),
        "agents_created": result.agents_created,
        "agents_skipped": result.agents_skipped,
        "agents_linked": 0,
        "schemas_processed": result.schemas_processed,
        "errors": result.errors,
    }

    uploaded = []
    with tempfile.TemporaryDirectory(prefix="musit-agent-migration-") as tmp:
        out = Path(tmp) / "report.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        uri = f"s3://{bucket}/{key}"
        log.info(f"Uploading MUSIT agent migration report to {uri}")
        upload_file_with_compat_retry(str(out), bucket, key)
        uploaded.append(uri)

    return uploaded


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
) -> dict:
    """Migrate MUSIT ACTOR rows to Specify ``Agent`` (no logins).

    Args:
        oracle_env: Oracle environment prefix (e.g. PROD, TEST).
        dry_run: When True, log only; no ``Agent`` rows are written.
        musit_schemas: Subset of ``MUSIT_BOTANIKK_FELLES`` and/or ``MUSIT_ZOOLOGI_ENTOMOLOGI``.
            Defaults to both when omitted (``None``).
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
        f"schemas={musit_schemas})"
    )

    setup_django()

    rows = extract_musit_actors(oracle_env, list(musit_schemas))
    result = load_musit_actors_to_specify(rows, dry_run=dry_run)

    logger.info(
        f"MUSIT agent migration complete: created={result.agents_created}, "
        f"skipped={result.agents_skipped}, errors={len(result.errors)}"
    )

    uploaded = _upload_report(result, oracle_env, dry_run, musit_schemas, rows)
    for uri in uploaded:
        logger.info(f"Uploaded report: {uri}")

    return {
        "agents_created": result.agents_created,
        "agents_skipped": result.agents_skipped,
        "oracle_actors_extracted": len(rows),
        "errors": result.errors,
        "schemas_processed": result.schemas_processed,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
    }
