"""Prefect flow: pull Specify users (+ agents, SSO, access) from test → staging."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger, task

from flows.lib.mariadb_logical_sync import (
    staging_endpoint_from_env,
    test_endpoint_via_tunnel,
)
from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_setup import setup_django
from flows.lib.ssh_tunnel import ssh_local_forward, ssh_tunnel_config_from_env
from flows.lib.user_sync_test_to_staging import sync_users_test_to_staging

REPORT_CATEGORY = "sync-users-test-to-staging"


@task(name="sync-users-test-to-staging-run")
def sync_users_test_to_staging_task(
    *,
    dry_run: bool,
    usernames: list[str] | None,
    include_policies: bool,
    include_principals: bool,
    update_existing_sso: bool,
) -> dict[str, Any]:
    logger = get_run_logger()
    staging = staging_endpoint_from_env()
    tunnel_cfg = ssh_tunnel_config_from_env()

    out: dict[str, Any] = {
        "dry_run": dry_run,
        "usernames": usernames,
        "include_policies": include_policies,
        "include_principals": include_principals,
        "update_existing_sso": update_existing_sso,
        "staging": {
            "host": staging.host,
            "port": staging.port,
            "database": staging.database,
            "user": staging.user,
        },
        "test_remote": {
            "host": tunnel_cfg.remote_host,
            "port": tunnel_cfg.remote_port,
            "bastion": f"{tunnel_cfg.bastion_user}@{tunnel_cfg.bastion_host}:{tunnel_cfg.bastion_port}",
            "local_forward_port": tunnel_cfg.local_port,
        },
    }

    setup_django()

    with ssh_local_forward(tunnel_cfg) as tun:
        test_ep = test_endpoint_via_tunnel(local_port=tun.local_port)
        logger.info(
            "tunnel up; syncing users test→staging dry_run=%s filter=%s",
            dry_run,
            usernames,
        )
        sync_out = sync_users_test_to_staging(
            dry_run=dry_run,
            usernames=usernames,
            include_policies=include_policies,
            include_principals=include_principals,
            update_existing_sso=update_existing_sso,
            test_ep=test_ep,
        )
        out.update(sync_out)

    result = out.get("result") or {}
    logger.info(
        "sync_users_test_to_staging: only_on_test=%s created=%s skipped_existing=%s "
        "sso_created=%s policies=%s roles=%s principals=%s errors=%s",
        out.get("only_on_test_count"),
        result.get("users_created"),
        result.get("users_skipped_existing"),
        result.get("sso_created"),
        result.get("policies_created"),
        result.get("roles_linked"),
        result.get("principals_linked"),
        len(result.get("errors") or []),
    )
    for name in out.get("only_on_test_names") or []:
        logger.info("only on test: %s", name)
    for err in result.get("errors") or []:
        logger.warning("%s", err)
    return out


@flow(
    name="Sync Users Test to Staging",
    description=(
        "Selective pull of SpecifyUser + linked Agent + spuserexternalid (+ optional "
        "policies/roles/group principals) from test MariaDB into staging via SSH tunnel. "
        "Default dry_run=true."
    ),
)
def sync_users_test_to_staging_flow(
    dry_run: bool = True,
    usernames: list[str] | None = None,
    include_policies: bool = True,
    include_principals: bool = True,
    update_existing_sso: bool = True,
) -> dict[str, Any]:
    """Pull users created on test into staging so a later full DB sync preserves them.

    Args:
        dry_run: When True, report what would be created without writing.
        usernames: Optional allow-list of SpecifyUser.Name values; None = all on test.
        include_policies: Copy ``spuserpolicy`` / ``spuserrole`` (roles matched by name).
        include_principals: Link shared Sp6 group principals (not per-user UserPrincipal).
        update_existing_sso: When a user already exists on staging, still copy missing
            ``spuserexternalid`` rows from test.
    """
    logger = get_run_logger()
    logger.info(
        "sync_users_test_to_staging_flow | dry_run=%s usernames=%s "
        "include_policies=%s include_principals=%s update_existing_sso=%s",
        dry_run,
        usernames,
        include_policies,
        include_principals,
        update_existing_sso,
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = sync_users_test_to_staging_task(
        dry_run=dry_run,
        usernames=usernames,
        include_policies=include_policies,
        include_principals=include_principals,
        update_existing_sso=update_existing_sso,
    )

    report = {
        "flow": "sync_users_test_to_staging",
        "ts": ts,
        **result,
    }
    s3_key = migration_report_s3_key(REPORT_CATEGORY, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info("Uploaded report: %s", uri)
    report["uploaded"] = uploaded
    return report
