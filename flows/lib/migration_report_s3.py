"""Shared S3 key layout for migration Prefect flow reports.

Reports live under **their own** prefix (not ``S3_PREFIX`` / ``oracle-schema``),
so schema snapshots and migration summaries do not share the same tree.

Environment:
    ``S3_BUCKET`` — required for upload (set on the worker).
    ``S3_MIGRATION_REPORTS_PREFIX`` — optional; default ``migration-reports``.
"""

from __future__ import annotations

import os

_DEFAULT_REPORTS_ROOT = "migration-reports"

# One folder segment per flow type (under ``S3_MIGRATION_REPORTS_PREFIX``).
REPORT_CATEGORY_APP_USERS_BRUKARAR = "application-users-usd-metadata-brukarar"
REPORT_CATEGORY_MUSIT_COLLECTION_AGENTS = "collection-agents-musit-actor-person-name"
REPORT_CATEGORY_SPECIFY_STRUCTURE_SYNC = "specify-structure-sync"
REPORT_CATEGORY_NORTAXA_DISCIPLINE_TREES = "nortaxa-discipline-trees"
REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY = "oracle-geography-to-specify"


def migration_reports_s3_root() -> str:
    """Top-level bucket prefix for all migration JSON reports."""
    root = (os.getenv("S3_MIGRATION_REPORTS_PREFIX") or _DEFAULT_REPORTS_ROOT).strip().strip("/")
    return root or _DEFAULT_REPORTS_ROOT


def migration_report_s3_prefix(report_category: str, ts: str) -> str:
    """Directory prefix for one run (no trailing slash), ending with ``…/{ts}``."""
    base = migration_reports_s3_root()
    return f"{base}/{report_category}/{ts}"


def migration_report_s3_key(
    report_category: str,
    ts: str,
    filename: str = "report.json",
) -> str:
    """Full S3 object key for the JSON report file."""
    return f"{migration_report_s3_prefix(report_category, ts)}/{filename}"
