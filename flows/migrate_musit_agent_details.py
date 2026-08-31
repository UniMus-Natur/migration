"""Prefect flow: fill missing MUSIT ACTOR details onto existing Specify Agents.

Prerequisite: ``migrate_musit_agents_flow`` has created Agents with remarks markers.

Does **not** recreate agents or name variants. Fills empty Agent fields, Address,
and AgentIdentifier rows from ``ACTOR`` columns (URL note, dates, address, etc.).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_MUSIT_AGENT_DETAILS,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.musit_agent_details import (
    actor_details_row_from_oracle,
    build_agent_details_patch,
    coerce_date,
    patch_is_noop,
    sql_actor_details,
)
from flows.lib.musit_agents_common import (
    ALLOWED_MUSIT_AGENT_SCHEMAS,
    load_actor_id_to_agent_id,
)
from flows.lib.oracle_connectivity import (
    create_oracle_connection,
    get_oracle_config_from_env,
)
from flows.lib.specify_setup import setup_django

_MAX_ERROR_LINES = 200
_PROGRESS_LOG_EVERY = 5000


@dataclass
class MusitAgentDetailsMigrationResult:
    actors_seen: int = 0
    agents_updated: int = 0
    agents_unchanged: int = 0
    agents_missing: int = 0
    addresses_created: int = 0
    identifiers_created: int = 0
    errors: list[str] = field(default_factory=list)
    schemas_processed: list[str] = field(default_factory=list)


@dataclass
class MusitAgentDetailsRunOutcome:
    result: MusitAgentDetailsMigrationResult
    oracle_actors_extracted: int
    oracle_rows_per_schema: dict[str, int]
    agents_mapped_per_schema: dict[str, int]


def _existing_identifier_keys(agent_id: int) -> set[tuple[str, str]]:
    from specifyweb.specify.models import Agentidentifier

    keys: set[tuple[str, str]] = set()
    for itype, ident in Agentidentifier.objects.filter(agent_id=agent_id).values_list(
        "identifiertype", "identifier"
    ):
        keys.add((itype or "", ident or ""))
    return keys


def _apply_patch(agent, patch, *, dry_run: bool, result: MusitAgentDetailsMigrationResult) -> None:
    from specifyweb.specify.models import Address, Agentidentifier

    if patch_is_noop(patch):
        result.agents_unchanged += 1
        return

    if dry_run:
        result.agents_updated += 1
        if patch.create_address:
            result.addresses_created += 1
        result.identifiers_created += len(patch.identifiers)
        return

    update_fields = list(patch.agent_fields.keys())
    for k, v in patch.agent_fields.items():
        setattr(agent, k, v)
    if patch.remarks is not None:
        agent.remarks = patch.remarks
        update_fields.append("remarks")
    if update_fields:
        agent.timestampmodified = datetime.now(timezone.utc)
        update_fields.append("timestampmodified")
        agent.save(update_fields=update_fields)

    if patch.create_address:
        Address.objects.create(agent_id=agent.id, **patch.address_fields)
        result.addresses_created += 1

    for ident in patch.identifiers:
        Agentidentifier.objects.create(
            agent_id=agent.id,
            identifier=ident.identifier,
            identifiertype=ident.identifier_type,
        )
        result.identifiers_created += 1

    result.agents_updated += 1


@task(retries=1, retry_delay_seconds=5)
def extract_and_load_musit_agent_details_task(
    oracle_env: str,
    schemas: list[str],
    dry_run: bool = True,
) -> MusitAgentDetailsRunOutcome:
    setup_django()
    logger = get_run_logger()
    from specifyweb.specify.models import Address, Agent

    for s in schemas:
        if s not in ALLOWED_MUSIT_AGENT_SCHEMAS:
            raise ValueError(f"Unsupported schema: {s}")

    result = MusitAgentDetailsMigrationResult()
    rows_per_schema: Counter[str] = Counter()
    agents_mapped_per_schema: dict[str, int] = {}
    total = 0

    config = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config)
    try:
        for schema in schemas:
            actor_to_agent = load_actor_id_to_agent_id(schema)
            agents_mapped_per_schema[schema] = len(actor_to_agent)
            logger.info(
                f"{schema}: {len(actor_to_agent)} Specify agents with MUSIT ACTOR markers"
            )

            sql = sql_actor_details(schema)
            with connection.cursor() as cur:
                cur.execute(sql)
                cols = [d[0].lower() for d in cur.description]
                schema_count = 0
                for raw in cur:
                    total += 1
                    schema_count += 1
                    rows_per_schema[schema] += 1
                    result.actors_seen += 1
                    row = actor_details_row_from_oracle(cols, raw)
                    agent_id = actor_to_agent.get(row["actor_id"])
                    if agent_id is None:
                        result.agents_missing += 1
                        continue

                    try:
                        agent = Agent.objects.only(
                            "id",
                            "email",
                            "url",
                            "dateofbirth",
                            "dateofdeath",
                            "text1",
                            "text2",
                            "text3",
                            "text4",
                            "text5",
                            "remarks",
                            "version",
                        ).get(pk=agent_id)
                        has_address = Address.objects.filter(agent_id=agent_id).exists()
                        existing_ids = _existing_identifier_keys(agent_id)
                        patch = build_agent_details_patch(
                            row,
                            agent_email=agent.email,
                            agent_url=agent.url,
                            agent_dob=coerce_date(agent.dateofbirth),
                            agent_dod=coerce_date(agent.dateofdeath),
                            agent_text1=agent.text1,
                            agent_text2=agent.text2,
                            agent_text3=agent.text3,
                            agent_text4=agent.text4,
                            agent_text5=agent.text5,
                            agent_remarks=agent.remarks,
                            has_address=has_address,
                            existing_identifier_keys=existing_ids,
                        )
                        if dry_run:
                            logger.debug(
                                f"[DRY RUN] ACTOR_ID={row['actor_id']} agent_id={agent_id} "
                                f"fields={list(patch.agent_fields)} "
                                f"address={patch.create_address} "
                                f"identifiers={len(patch.identifiers)} "
                                f"remarks_update={patch.remarks is not None}"
                            )
                        _apply_patch(agent, patch, dry_run=dry_run, result=result)
                    except Exception as exc:
                        msg = (
                            f"Error filling details for {schema}.ACTOR_ID={row['actor_id']} "
                            f"(agent_id={agent_id}): {exc}"
                        )
                        logger.error(msg)
                        if len(result.errors) < _MAX_ERROR_LINES:
                            result.errors.append(msg)
                        elif len(result.errors) == _MAX_ERROR_LINES:
                            result.errors.append(
                                f"... further errors omitted (cap {_MAX_ERROR_LINES}); "
                                "see worker logs"
                            )

                    if total % _PROGRESS_LOG_EVERY == 0:
                        logger.info(
                            f"{'[DRY RUN] ' if dry_run else ''}progress: {total} actors "
                            f"(updated={result.agents_updated}, "
                            f"unchanged={result.agents_unchanged}, "
                            f"missing={result.agents_missing}, "
                            f"identifiers={result.identifiers_created})"
                        )

            logger.info(f"Finished scanning {schema}.ACTOR ({schema_count} rows)")
    finally:
        connection.close()

    result.schemas_processed = sorted(schemas)
    logger.info(
        f"MUSIT agent-details extract+load done: oracle_rows={total}, "
        f"updated={result.agents_updated}, unchanged={result.agents_unchanged}, "
        f"addresses={result.addresses_created}, identifiers={result.identifiers_created}, "
        f"missing={result.agents_missing}, errors={len(result.errors)}"
    )
    return MusitAgentDetailsRunOutcome(
        result=result,
        oracle_actors_extracted=total,
        oracle_rows_per_schema=dict(sorted(rows_per_schema.items())),
        agents_mapped_per_schema=agents_mapped_per_schema,
    )


def _report_dict(
    ts: str,
    oracle_env: str,
    dry_run: bool,
    musit_schemas: list[str],
    outcome: MusitAgentDetailsRunOutcome,
) -> dict:
    r = outcome.result
    return {
        "report_version": 1,
        "flow": "migrate_musit_agent_details",
        "migration_phase": "1.1c",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "musit_schemas": list(musit_schemas),
        "oracle_actors_extracted": outcome.oracle_actors_extracted,
        "oracle_rows_per_schema": outcome.oracle_rows_per_schema,
        "agents_mapped_per_schema": outcome.agents_mapped_per_schema,
        "agents_updated": r.agents_updated,
        "agents_unchanged": r.agents_unchanged,
        "agents_missing": r.agents_missing,
        "addresses_created": r.addresses_created,
        "identifiers_created": r.identifiers_created,
        "schemas_processed": r.schemas_processed,
        "errors": r.errors,
    }


@flow(
    name="Migrate MUSIT Agent Details",
    description=(
        "Phase 1.1c: Fill ACTOR URL/note/address/dates/etc. onto existing Agents "
        "(no agent re-create)."
    ),
)
def migrate_musit_agent_details_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    musit_schemas: list[str] | None = None,
) -> dict:
    """Backfill MUSIT person-module fields onto existing Specify Agents."""
    logger = get_run_logger()
    if musit_schemas is None:
        musit_schemas = ["MUSIT_BOTANIKK_FELLES", "MUSIT_ZOOLOGI_ENTOMOLOGI"]

    for s in musit_schemas:
        if s not in ALLOWED_MUSIT_AGENT_SCHEMAS:
            raise ValueError(
                f"Invalid musit_schemas entry {s!r}; "
                f"allowed: {sorted(ALLOWED_MUSIT_AGENT_SCHEMAS)}"
            )

    logger.info(
        f"Starting MUSIT agent-details fill-in (oracle_env={oracle_env}, "
        f"dry_run={dry_run}, schemas={musit_schemas})"
    )

    setup_django()
    outcome = extract_and_load_musit_agent_details_task(
        oracle_env, list(musit_schemas), dry_run
    )
    result = outcome.result

    logger.info(
        f"MUSIT agent-details complete: updated={result.agents_updated}, "
        f"addresses={result.addresses_created}, "
        f"identifiers={result.identifiers_created}, "
        f"missing={result.agents_missing}, errors={len(result.errors)}"
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = _report_dict(ts, oracle_env, dry_run, musit_schemas, outcome)
    s3_key = migration_report_s3_key(REPORT_CATEGORY_MUSIT_AGENT_DETAILS, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info(f"Uploaded report: {uri}")

    return {
        "agents_updated": result.agents_updated,
        "agents_unchanged": result.agents_unchanged,
        "agents_missing": result.agents_missing,
        "addresses_created": result.addresses_created,
        "identifiers_created": result.identifiers_created,
        "oracle_actors_extracted": outcome.oracle_actors_extracted,
        "errors": result.errors,
        "schemas_processed": result.schemas_processed,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
    }
