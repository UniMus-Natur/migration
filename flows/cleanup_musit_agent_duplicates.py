"""Prefect flow: repair mangled MUSIT agent markers and delete unused duplicates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_MUSIT_AGENT_DUP_CLEANUP,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.musit_agent_duplicate_cleanup import (
    MusitAgentDuplicateCleanupResult,
    apply_cleanup_plan,
    collect_candidates_for_actor,
    load_all_musit_agent_indexes,
    plan_actor_cleanup,
    sql_actors_for_cleanup,
)
from flows.lib.musit_agents_common import ALLOWED_MUSIT_AGENT_SCHEMAS
from flows.lib.oracle_connectivity import (
    create_oracle_connection,
    get_oracle_config_from_env,
)
from flows.lib.specify_setup import setup_django

_PROGRESS_LOG_EVERY = 5000
_MAX_ERROR_LINES = 200


@dataclass
class CleanupRunOutcome:
    result: MusitAgentDuplicateCleanupResult
    oracle_actors_extracted: int
    oracle_rows_per_schema: dict[str, int]


@task(retries=1, retry_delay_seconds=5)
def cleanup_musit_agent_duplicates_task(
    oracle_env: str,
    schemas: list[str],
    dry_run: bool = True,
) -> CleanupRunOutcome:
    setup_django()
    logger = get_run_logger()
    from django.db import connection as django_connection

    for s in schemas:
        if s not in ALLOWED_MUSIT_AGENT_SCHEMAS:
            raise ValueError(f"Unsupported schema: {s}")

    result = MusitAgentDuplicateCleanupResult()
    rows_per_schema: Counter[str] = Counter()
    total = 0

    logger.info("Indexing Specify Agents with MUSIT remarks…")
    by_schema_actor, orphans_by_text1, unmatched_orphans = load_all_musit_agent_indexes(
        schemas
    )
    result.orphans_unmatched = len(unmatched_orphans)
    logger.info(
        f"Indexed marker agents per schema: "
        + ", ".join(
            f"{sch}={sum(len(v) for v in by_actor.values())}"
            for sch, by_actor in sorted(by_schema_actor.items())
        )
        + f"; oracle_note orphans with text1={sum(len(v) for v in orphans_by_text1.values())}; "
        f"orphans without text1={len(unmatched_orphans)}"
    )

    claimed_orphan_ids: set[int] = set()
    config = get_oracle_config_from_env(oracle_env)
    oracle = create_oracle_connection(config)
    try:
        for schema in schemas:
            by_actor = by_schema_actor.get(schema.strip().upper(), {})
            sql = sql_actors_for_cleanup(schema)
            with oracle.cursor() as cur:
                cur.execute(sql)
                cols = [d[0].lower() for d in cur.description]
                schema_count = 0
                with django_connection.cursor() as db_cur:
                    for raw in cur:
                        total += 1
                        schema_count += 1
                        rows_per_schema[schema] += 1
                        result.actors_seen += 1
                        row = dict(zip(cols, raw))
                        actor_id = int(row["actor_id"])
                        actorname = row.get("actorname")
                        actor_type = (
                            int(row["actor_type"])
                            if row.get("actor_type") is not None
                            else None
                        )
                        candidates = collect_candidates_for_actor(
                            actor_id=actor_id,
                            actorname=actorname,
                            by_actor=by_actor,
                            orphans_by_text1=orphans_by_text1,
                            claimed_orphan_ids=claimed_orphan_ids,
                        )
                        if not candidates:
                            continue
                        result.actors_with_candidates += 1
                        plan = plan_actor_cleanup(
                            schema=schema,
                            actor_id=actor_id,
                            actorname=actorname,
                            actor_type=actor_type,
                            candidates=candidates,
                        )
                        if plan is None:
                            continue
                        if dry_run and (
                            plan.restore_remarks or plan.name_updates or plan.delete_ids
                        ):
                            logger.debug(
                                f"[DRY RUN] {schema}.ACTOR_ID={actor_id} "
                                f"keeper={plan.keeper_id} delete={plan.delete_ids} "
                                f"remarks_fix={plan.restore_remarks is not None} "
                                f"name_fields={list(plan.name_updates)}"
                            )
                        apply_cleanup_plan(
                            plan,
                            dry_run=dry_run,
                            db_cursor=db_cur,
                            result=result,
                        )
                        if total % _PROGRESS_LOG_EVERY == 0:
                            logger.info(
                                f"{'[DRY RUN] ' if dry_run else ''}progress: {total} actors "
                                f"(repaired={result.keepers_repaired}, "
                                f"deleted={result.duplicates_deleted}, "
                                f"unchanged={result.keepers_unchanged}, "
                                f"blocked={result.skipped_blocked_fk})"
                            )

            logger.info(f"Finished {schema}.ACTOR ({schema_count} rows)")
    finally:
        oracle.close()

    # Orphans never claimed by any ACTORNAME match
    still_unclaimed = 0
    for group in orphans_by_text1.values():
        for c in group:
            if c.agent_id not in claimed_orphan_ids:
                still_unclaimed += 1
    result.orphans_unmatched += still_unclaimed
    result.schemas_processed = sorted(schemas)

    if len(result.errors) > _MAX_ERROR_LINES:
        result.errors = result.errors[:_MAX_ERROR_LINES] + [
            f"... further errors omitted (cap {_MAX_ERROR_LINES})"
        ]

    logger.info(
        f"Cleanup done: actors_seen={result.actors_seen}, "
        f"with_candidates={result.actors_with_candidates}, "
        f"repaired={result.keepers_repaired}, deleted={result.duplicates_deleted}, "
        f"unchanged={result.keepers_unchanged}, blocked_fk={result.skipped_blocked_fk}, "
        f"orphans_unmatched={result.orphans_unmatched}, errors={len(result.errors)}"
    )
    return CleanupRunOutcome(
        result=result,
        oracle_actors_extracted=total,
        oracle_rows_per_schema=dict(sorted(rows_per_schema.items())),
    )


def _report_dict(
    ts: str,
    oracle_env: str,
    dry_run: bool,
    musit_schemas: list[str],
    outcome: CleanupRunOutcome,
) -> dict:
    r = outcome.result
    return {
        "report_version": 1,
        "flow": "cleanup_musit_agent_duplicates",
        "migration_phase": "1.1-cleanup",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "musit_schemas": list(musit_schemas),
        "oracle_actors_extracted": outcome.oracle_actors_extracted,
        "oracle_rows_per_schema": outcome.oracle_rows_per_schema,
        "actors_seen": r.actors_seen,
        "actors_with_candidates": r.actors_with_candidates,
        "keepers_repaired": r.keepers_repaired,
        "keepers_unchanged": r.keepers_unchanged,
        "duplicates_deleted": r.duplicates_deleted,
        "skipped_blocked_fk": r.skipped_blocked_fk,
        "orphans_unmatched": r.orphans_unmatched,
        "schemas_processed": r.schemas_processed,
        "errors": r.errors,
    }


@flow(
    name="Cleanup MUSIT Agent Duplicates",
    description=(
        "Repair oracle_note-only remarks markers, refresh names from ACTORNAME, "
        "and delete unused duplicate Agents created by update_existing_names."
    ),
)
def cleanup_musit_agent_duplicates_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    musit_schemas: list[str] | None = None,
) -> dict:
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
        f"Starting MUSIT agent duplicate cleanup "
        f"(oracle_env={oracle_env}, dry_run={dry_run}, schemas={musit_schemas})"
    )
    setup_django()
    outcome = cleanup_musit_agent_duplicates_task(
        oracle_env, list(musit_schemas), dry_run
    )
    r = outcome.result

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = _report_dict(ts, oracle_env, dry_run, musit_schemas, outcome)
    s3_key = migration_report_s3_key(REPORT_CATEGORY_MUSIT_AGENT_DUP_CLEANUP, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info(f"Uploaded report: {uri}")

    return {
        "keepers_repaired": r.keepers_repaired,
        "duplicates_deleted": r.duplicates_deleted,
        "keepers_unchanged": r.keepers_unchanged,
        "skipped_blocked_fk": r.skipped_blocked_fk,
        "orphans_unmatched": r.orphans_unmatched,
        "oracle_actors_extracted": outcome.oracle_actors_extracted,
        "errors": r.errors,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
    }
