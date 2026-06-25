"""Prefect flow: sync Specify 7 divisions, disciplines, and collections from YAML."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_SPECIFY_STRUCTURE_SYNC,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_structure.config import load_structure_config
from flows.lib.specify_structure.reconcile import reconcile_structure
from flows.lib.specify_setup import setup_django

_DEFAULT_CONFIG = str(
    Path(__file__).resolve().parent.parent / "config" / "specify_structure" / "unimus_natur.yaml"
)


def _structure_report_dict(
    *,
    ts: str,
    config_path: str,
    dry_run: bool,
    result,
) -> dict:
    return {
        "flow": "sync_specify_structure",
        "timestamp_utc": ts,
        "config_path": config_path,
        "dry_run": dry_run,
        "divisions_created": result.divisions_created,
        "divisions_skipped": result.divisions_skipped,
        "disciplines_created": result.disciplines_created,
        "disciplines_skipped": result.disciplines_skipped,
        "taxon_trees_created": result.taxon_trees_created,
        "collections_created": result.collections_created,
        "collections_skipped": result.collections_skipped,
        "warnings": result.warnings,
        "errors": result.errors,
    }


@flow(
    name="Sync Specify structure",
    description=(
        "Post-bootstrap: read YAML (divisions, disciplines, collections) and create "
        "missing rows in Specify via Django ORM and setup_tool helpers. Idempotent."
    ),
)
def sync_specify_structure_flow(
    config_path: str = _DEFAULT_CONFIG,
    dry_run: bool = True,
) -> dict:
    """Reconcile Specify hierarchy with ``config_path`` (default: UniMus Natur example).

    Args:
        config_path: Path to structure YAML.
        dry_run: When True, only log counters and skip writes.
    """
    logger = get_run_logger()
    path = Path(config_path).resolve()
    logger.info("Loading structure config from %s", path)
    config = load_structure_config(path)

    # Must run before reconcile_structure (imports specifyweb lazily there).
    setup_django()
    result = reconcile_structure(config, dry_run=dry_run)

    logger.info(
        "Structure sync finished: divisions +%s ~%s, disciplines +%s ~%s, "
        "taxon trees +%s, collections +%s ~%s",
        result.divisions_created,
        result.divisions_skipped,
        result.disciplines_created,
        result.disciplines_skipped,
        result.taxon_trees_created,
        result.collections_created,
        result.collections_skipped,
    )
    if result.warnings:
        for w in result.warnings:
            logger.warning("%s", w)
    if result.errors:
        for e in result.errors:
            logger.error("%s", e)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = _structure_report_dict(ts=ts, config_path=str(path), dry_run=dry_run, result=result)
    s3_key = migration_report_s3_key(REPORT_CATEGORY_SPECIFY_STRUCTURE_SYNC, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    if uploaded:
        for uri in uploaded:
            logger.info("Report: %s", uri)

    return {
        **report,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
    }
