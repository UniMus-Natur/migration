"""Prefect flow: fill Specify AgentVariant from alternate MUSIT PERSON_NAME rows.

Prerequisite: ``migrate_musit_agents_flow`` has already created ``Agent`` rows with
``remarks`` markers ``MUSIT-migration: ACTOR; schema=…; ACTOR_ID=…``.

This flow does **not** recreate agents. It only attaches non-preferred
``PERSON_NAME`` spellings as ``AgentVariant`` (varType Variant = 0).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_MUSIT_AGENT_VARIANTS,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.musit_agent_variants import (
    AGENT_VARIANT_VARTYPE_VARIANT,
    format_person_name_variant,
    should_skip_variant_name,
    sql_alternate_person_names,
    variant_row_from_oracle,
)
from flows.lib.musit_agents_common import (
    ALLOWED_MUSIT_AGENT_SCHEMAS,
    parse_actor_id_from_agent_remarks,
)
from flows.lib.oracle_connectivity import (
    create_oracle_connection,
    get_oracle_config_from_env,
)
from flows.lib.specify_setup import setup_django

_MAX_ERROR_LINES = 200
_PROGRESS_LOG_EVERY = 5000


@dataclass
class MusitAgentVariantMigrationResult:
    variants_created: int = 0
    variants_skipped_duplicate: int = 0
    variants_skipped_empty: int = 0
    person_names_seen: int = 0
    agents_missing: int = 0
    errors: list[str] = field(default_factory=list)
    schemas_processed: list[str] = field(default_factory=list)


@dataclass
class MusitAgentVariantRunOutcome:
    result: MusitAgentVariantMigrationResult
    oracle_person_names_extracted: int
    oracle_rows_per_schema: dict[str, int]
    agents_mapped_per_schema: dict[str, int]


def _load_actor_to_agent_id(schema: str) -> dict[int, int]:
    """Map MUSIT ``ACTOR_ID`` → Specify ``Agent.id`` via remarks markers."""
    from specifyweb.specify.models import Agent

    prefix = f"MUSIT-migration: ACTOR; schema={schema};"
    out: dict[int, int] = {}
    qs = Agent.objects.filter(remarks__startswith=prefix).only("id", "remarks")
    for agent in qs.iterator(chunk_size=5000):
        actor_id = parse_actor_id_from_agent_remarks(agent.remarks, schema)
        if actor_id is not None:
            out[actor_id] = int(agent.id)
    return out


def _existing_variant_names(agent_id: int) -> set[str]:
    from specifyweb.specify.models import Agentvariant

    names = Agentvariant.objects.filter(agent_id=agent_id).values_list("name", flat=True)
    return {n for n in names if n}


@task(retries=1, retry_delay_seconds=5)
def extract_and_load_musit_agent_variants_task(
    oracle_env: str,
    schemas: list[str],
    dry_run: bool = True,
) -> MusitAgentVariantRunOutcome:
    """Stream alternate PERSON_NAME rows and create AgentVariant on existing Agents."""
    setup_django()
    logger = get_run_logger()
    from specifyweb.specify.models import Agentvariant

    for s in schemas:
        if s not in ALLOWED_MUSIT_AGENT_SCHEMAS:
            raise ValueError(f"Unsupported schema: {s}")

    result = MusitAgentVariantMigrationResult()
    rows_per_schema: Counter[str] = Counter()
    agents_mapped_per_schema: dict[str, int] = {}
    total = 0

    config = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config)
    try:
        for schema in schemas:
            actor_to_agent = _load_actor_to_agent_id(schema)
            agents_mapped_per_schema[schema] = len(actor_to_agent)
            logger.info(
                f"{schema}: {len(actor_to_agent)} Specify agents with MUSIT ACTOR markers"
            )

            sql = sql_alternate_person_names(schema)
            current_actor_id: int | None = None
            current_agent_id: int | None = None
            existing_names: set[str] = set()

            with connection.cursor() as cur:
                cur.execute(sql)
                cols = [d[0].lower() for d in cur.description]
                schema_count = 0
                for raw in cur:
                    total += 1
                    schema_count += 1
                    rows_per_schema[schema] += 1
                    result.person_names_seen += 1
                    row = variant_row_from_oracle(cols, raw)
                    actor_id = row["actor_id"]

                    if actor_id != current_actor_id:
                        current_actor_id = actor_id
                        current_agent_id = actor_to_agent.get(actor_id)
                        if current_agent_id is not None:
                            existing_names = _existing_variant_names(current_agent_id)
                        else:
                            existing_names = set()

                    if current_agent_id is None:
                        result.agents_missing += 1
                        continue

                    name = format_person_name_variant(
                        row["person_surname"],
                        row["person_given_name"],
                        row["person_middle_name"],
                    )
                    skip = should_skip_variant_name(name, existing_names)
                    if skip == "empty":
                        result.variants_skipped_empty += 1
                        continue
                    if skip == "duplicate":
                        result.variants_skipped_duplicate += 1
                        continue

                    assert name is not None
                    if dry_run:
                        logger.debug(
                            f"[DRY RUN] Would create AgentVariant agent_id={current_agent_id} "
                            f"schema={schema} ACTOR_ID={actor_id} "
                            f"PERSON_NAME_ID={row['person_name_id']} name={name!r}"
                        )
                        result.variants_created += 1
                        existing_names.add(name)
                    else:
                        try:
                            Agentvariant.objects.create(
                                agent_id=current_agent_id,
                                name=name,
                                vartype=AGENT_VARIANT_VARTYPE_VARIANT,
                            )
                            result.variants_created += 1
                            existing_names.add(name)
                        except Exception as exc:
                            msg = (
                                f"Error creating AgentVariant for {schema}."
                                f"PERSON_NAME_ID={row['person_name_id']} "
                                f"(ACTOR_ID={actor_id}): {exc}"
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
                            f"{'[DRY RUN] ' if dry_run else ''}progress: {total} alternate "
                            f"PERSON_NAME rows scanned (created={result.variants_created}, "
                            f"dup={result.variants_skipped_duplicate}, "
                            f"missing_agent={result.agents_missing})"
                        )

            logger.info(
                f"Finished scanning {schema}.PERSON_NAME alternates ({schema_count} rows)"
            )
    finally:
        connection.close()

    result.schemas_processed = sorted(schemas)
    logger.info(
        f"MUSIT agent-variant extract+load done: oracle_rows={total}, "
        f"created={result.variants_created}, "
        f"skipped_dup={result.variants_skipped_duplicate}, "
        f"skipped_empty={result.variants_skipped_empty}, "
        f"agents_missing={result.agents_missing}, errors={len(result.errors)}"
    )
    return MusitAgentVariantRunOutcome(
        result=result,
        oracle_person_names_extracted=total,
        oracle_rows_per_schema=dict(sorted(rows_per_schema.items())),
        agents_mapped_per_schema=agents_mapped_per_schema,
    )


def _report_dict(
    ts: str,
    oracle_env: str,
    dry_run: bool,
    musit_schemas: list[str],
    outcome: MusitAgentVariantRunOutcome,
) -> dict:
    r = outcome.result
    return {
        "report_version": 1,
        "flow": "migrate_musit_agent_variants",
        "migration_phase": "1.1b",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "musit_schemas": list(musit_schemas),
        "oracle_person_names_extracted": outcome.oracle_person_names_extracted,
        "oracle_rows_per_schema": outcome.oracle_rows_per_schema,
        "agents_mapped_per_schema": outcome.agents_mapped_per_schema,
        "variants_created": r.variants_created,
        "variants_skipped_duplicate": r.variants_skipped_duplicate,
        "variants_skipped_empty": r.variants_skipped_empty,
        "agents_missing": r.agents_missing,
        "schemas_processed": r.schemas_processed,
        "errors": r.errors,
    }


@flow(
    name="Migrate MUSIT Agent Variants",
    description=(
        "Phase 1.1b: Load alternate MUSIT PERSON_NAME rows as Specify AgentVariant "
        "on Agents already created by Migrate MUSIT Actors (no agent re-create)."
    ),
)
def migrate_musit_agent_variants_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    musit_schemas: list[str] | None = None,
) -> dict:
    """Attach synonymic MUSIT person names as ``AgentVariant``.

    Args:
        oracle_env: Oracle environment prefix (e.g. PROD, TEST).
        dry_run: When True, log only; no ``AgentVariant`` rows are written.
        musit_schemas: Subset of allowed MUSIT schemas; defaults to both.
    """
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
        f"Starting MUSIT agent-variant migration (oracle_env={oracle_env}, "
        f"dry_run={dry_run}, schemas={musit_schemas})"
    )

    setup_django()
    outcome = extract_and_load_musit_agent_variants_task(
        oracle_env, list(musit_schemas), dry_run
    )
    result = outcome.result

    logger.info(
        f"MUSIT agent-variant migration complete: created={result.variants_created}, "
        f"skipped_dup={result.variants_skipped_duplicate}, "
        f"agents_missing={result.agents_missing}, errors={len(result.errors)}"
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = _report_dict(ts, oracle_env, dry_run, musit_schemas, outcome)
    s3_key = migration_report_s3_key(REPORT_CATEGORY_MUSIT_AGENT_VARIANTS, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info(f"Uploaded report: {uri}")

    return {
        "variants_created": result.variants_created,
        "variants_skipped_duplicate": result.variants_skipped_duplicate,
        "variants_skipped_empty": result.variants_skipped_empty,
        "agents_missing": result.agents_missing,
        "oracle_person_names_extracted": outcome.oracle_person_names_extracted,
        "errors": result.errors,
        "schemas_processed": result.schemas_processed,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
    }
