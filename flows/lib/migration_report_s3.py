"""Shared S3 key layout for migration Prefect flow reports.

All reports go under ``{S3_PREFIX}/migration-reports/…`` so the bucket stays
organized for application-user loads, collection-agent loads, and future flows.

Environment variables (unchanged): ``S3_BUCKET``, ``S3_PREFIX`` (optional).
"""

from __future__ import annotations

# Umbrella folder under S3_PREFIX (single place to list “human migration artifacts”).
MIGRATION_REPORTS_ROOT = "migration-reports"

# Descriptive path segments (no secrets; stable for dashboards and IAM prefixes).
SPECIFY7_APP_USERS_BRUKARAR = "specify7/application-users-usd-metadata-brukarar"
SPECIFY7_COLLECTION_AGENTS_ACTOR = "specify7/collection-agents-musit-actor-person-name"


def migration_report_s3_prefix(env_prefix: str, report_subpath: str, ts: str) -> str:
    """Directory prefix for one run (no trailing slash), ending with ``…/{ts}``."""
    root = env_prefix.strip().strip("/")
    tail = f"{MIGRATION_REPORTS_ROOT}/{report_subpath}/{ts}"
    return f"{root}/{tail}" if root else tail


def migration_report_s3_key(
    env_prefix: str,
    report_subpath: str,
    ts: str,
    filename: str = "report.json",
) -> str:
    """Full S3 object key for the JSON report file."""
    return f"{migration_report_s3_prefix(env_prefix, report_subpath, ts)}/{filename}"
