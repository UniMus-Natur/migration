"""Prefect flow: migrate application users from Oracle (USD_METADATA) to Specify 7.

Source tables
    USD_METADATA.BRUKARAR          — centralised user directory
    USD_METADATA.BRUKERNAVN_GRUPPE — user ↔ group memberships
    USD_METADATA.GRUPPE            — groups with museum affiliation

Target tables (via Specify Django ORM)
    specifyuser  — login account
    agent        — person record linked to the login account

This is Phase 1.4 of the migration strategy.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, task, get_run_logger

from flows.lib.oracle_connectivity import (
    create_oracle_connection,
    get_oracle_config_from_env,
)
from flows.lib.s3_connectivity import upload_file_with_compat_retry
from flows.lib.specify_setup import setup_django


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OracleGroup:
    gruppe_id: int
    navn: str
    museum: str | None
    forklaring: str | None


@dataclass
class OracleUser:
    usr: str
    namn: str | None
    epost: str | None
    telefon: str | None
    institusjon: str | None
    feide: str | None
    feide_epost: str | None
    adresse: str | None
    adm: str | None
    oppretta: str | None
    groups: list[OracleGroup] = field(default_factory=list)
    default_group: OracleGroup | None = None


# ---------------------------------------------------------------------------
# Oracle extraction
# ---------------------------------------------------------------------------

@task(retries=2, retry_delay_seconds=5)
def extract_oracle_users(oracle_env: str) -> list[OracleUser]:
    """Pull users, groups, and memberships from USD_METADATA."""
    logger = get_run_logger()
    config = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config)

    try:
        with connection.cursor() as cur:
            cur.execute("SELECT GRUPPEID, NAVN, MUSEUM, FORKLARING FROM USD_METADATA.GRUPPE")
            groups_by_id: dict[int, OracleGroup] = {}
            for row in cur.fetchall():
                gid = int(row[0])
                groups_by_id[gid] = OracleGroup(
                    gruppe_id=gid,
                    navn=row[1] or "",
                    museum=row[2],
                    forklaring=row[3],
                )
            logger.info(f"Fetched {len(groups_by_id)} groups from USD_METADATA.GRUPPE")

        with connection.cursor() as cur:
            cur.execute("""
                SELECT BRUKERNAVN, GRUPPEID, DEFAULTGRUPPE
                FROM USD_METADATA.BRUKERNAVN_GRUPPE
            """)
            memberships: dict[str, list[tuple[int, bool]]] = {}
            for brukernavn, gruppe_id, default_flag in cur.fetchall():
                if brukernavn is None:
                    continue
                is_default = str(default_flag or "").strip().upper() in ("J", "Y", "1", "TRUE")
                memberships.setdefault(brukernavn, []).append((int(gruppe_id), is_default))
            logger.info(f"Fetched {sum(len(v) for v in memberships.values())} memberships")

        with connection.cursor() as cur:
            cur.execute("""
                SELECT USR, NAMN, EPOST, TELEFON, INSTITUSJON,
                       FEIDE, FEIDE_EPOST, ADRESSE, ADM, OPPRETTA
                FROM USD_METADATA.BRUKARAR
                ORDER BY USR
            """)
            columns = [d[0].lower() for d in cur.description]
            users: list[OracleUser] = []
            for raw in cur.fetchall():
                row = dict(zip(columns, raw))
                usr = row["usr"]
                user = OracleUser(
                    usr=usr,
                    namn=row.get("namn"),
                    epost=row.get("epost"),
                    telefon=row.get("telefon"),
                    institusjon=row.get("institusjon"),
                    feide=row.get("feide"),
                    feide_epost=row.get("feide_epost"),
                    adresse=row.get("adresse"),
                    adm=row.get("adm"),
                    oppretta=(
                        row["oppretta"].isoformat()
                        if hasattr(row.get("oppretta"), "isoformat")
                        else str(row.get("oppretta") or "")
                    ),
                )
                for gruppe_id, is_default in memberships.get(usr, []):
                    grp = groups_by_id.get(gruppe_id)
                    if grp:
                        user.groups.append(grp)
                        if is_default:
                            user.default_group = grp
                users.append(user)
            logger.info(f"Fetched {len(users)} users from USD_METADATA.BRUKARAR")
    finally:
        connection.close()

    return users


# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------

_COMMA_RE = re.compile(r",\s*")


def parse_name(namn: str | None) -> tuple[str | None, str]:
    """Split a display name into (first_name, last_name).

    Handles "Lastname, Firstname" and "Firstname Lastname" conventions.
    Returns (None, namn) as a fallback when the name cannot be split.
    """
    if not namn or not namn.strip():
        return None, "(unknown)"

    namn = namn.strip()

    if "," in namn:
        parts = _COMMA_RE.split(namn, maxsplit=1)
        last = parts[0].strip()
        first = parts[1].strip() if len(parts) > 1 else None
        return first or None, last

    parts = namn.rsplit(maxsplit=1)
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# Specify loading
# ---------------------------------------------------------------------------

@dataclass
class MigrationResult:
    users_created: int = 0
    users_skipped: int = 0
    agents_created: int = 0
    agents_linked: int = 0
    errors: list[str] = field(default_factory=list)
    group_mapping: list[dict] = field(default_factory=list)


@task(retries=1, retry_delay_seconds=3)
def load_users_to_specify(
    oracle_users: list[OracleUser],
    dry_run: bool = True,
) -> MigrationResult:
    """Create SpecifyUser + Agent records via the Django ORM."""
    logger = get_run_logger()

    from specifyweb.specify.models import Agent, Specifyuser, Division

    division = Division.objects.first()
    if division is None:
        raise RuntimeError("No Division found in Specify — the database must be initialized first")

    result = MigrationResult()

    # Collect distinct museum names for the report
    museums_seen: dict[str, set[str]] = {}

    for ou in oracle_users:
        username = ou.usr.strip()
        if not username:
            continue

        # Track group → museum mapping per user
        for grp in ou.groups:
            museums_seen.setdefault(grp.museum or "(none)", set()).add(grp.navn)

        # Check for existing SpecifyUser
        existing = Specifyuser.objects.filter(name=username).first()
        if existing is not None:
            logger.debug(f"SpecifyUser '{username}' already exists (id={existing.id}), skipping")
            result.users_skipped += 1
            continue

        email = (ou.feide_epost or ou.epost or "").strip() or None
        first_name, last_name = parse_name(ou.namn)

        if dry_run:
            logger.info(
                f"[DRY RUN] Would create SpecifyUser '{username}' + "
                f"Agent '{first_name or ''} {last_name}' "
                f"(email={email}, groups={[g.navn for g in ou.groups]})"
            )
            result.users_created += 1
            result.agents_created += 1
            continue

        try:
            sp_user = Specifyuser(
                name=username,
                email=email,
                usertype="Manager",
                isloggedin=False,
                isloggedinreport=False,
            )
            sp_user.set_unusable_password()
            sp_user.save()
            result.users_created += 1
            logger.info(f"Created SpecifyUser '{username}' (id={sp_user.id})")

            agent = Agent(
                agenttype=1,  # Person
                firstname=first_name,
                lastname=last_name,
                email=email,
                division=division,
                specifyuser=sp_user,
                remarks=_build_agent_remarks(ou),
            )
            agent.save()
            result.agents_created += 1
            result.agents_linked += 1
            logger.info(f"Created Agent '{first_name or ''} {last_name}' (id={agent.id}) → SpecifyUser {sp_user.id}")

        except Exception as exc:
            msg = f"Error migrating user '{username}': {exc}"
            logger.error(msg)
            result.errors.append(msg)

    # Build group → museum summary for the report
    for museum, group_names in sorted(museums_seen.items()):
        result.group_mapping.append({
            "museum": museum,
            "groups": sorted(group_names),
        })

    return result


def _build_agent_remarks(ou: OracleUser) -> str:
    """Encode Oracle provenance into Agent.Remarks for traceability."""
    parts = [f"MUSIT-migration: USR={ou.usr}"]
    if ou.feide:
        parts.append(f"FEIDE={ou.feide}")
    if ou.institusjon:
        parts.append(f"institution={ou.institusjon}")
    if ou.adm:
        parts.append(f"admin={ou.adm}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Artifact upload
# ---------------------------------------------------------------------------

def _upload_report(result: MigrationResult, oracle_env: str, dry_run: bool) -> list[str]:
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        return []

    prefix = os.getenv("S3_PREFIX", "oracle-schema").strip("/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{prefix}/user-migration/{ts}" if prefix else f"user-migration/{ts}"

    report = {
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "users_created": result.users_created,
        "users_skipped": result.users_skipped,
        "agents_created": result.agents_created,
        "agents_linked": result.agents_linked,
        "errors": result.errors,
        "group_to_museum_mapping": result.group_mapping,
    }

    uploaded = []
    with tempfile.TemporaryDirectory(prefix="user-migration-") as tmp:
        out = Path(tmp) / "migration_report.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        key = f"{base}/migration_report.json"
        upload_file_with_compat_retry(str(out), bucket, key)
        uploaded.append(f"s3://{bucket}/{key}")

    return uploaded


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

@flow(
    name="Migrate Users",
    description=(
        "Phase 1.4: Extract application users from Oracle USD_METADATA "
        "(BRUKARAR + GRUPPE) and create SpecifyUser + Agent records "
        "in Specify 7 via Django ORM."
    ),
)
def migrate_users_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
) -> dict:
    """Migrate MUSIT application users to Specify 7.

    Args:
        oracle_env: Oracle environment prefix (PROD or TEST).
        dry_run: When True, log what would be created without writing to Specify.
    """
    logger = get_run_logger()
    logger.info(f"Starting user migration (oracle_env={oracle_env}, dry_run={dry_run})")

    setup_django()

    oracle_users = extract_oracle_users(oracle_env)
    result = load_users_to_specify(oracle_users, dry_run=dry_run)

    logger.info(
        f"Migration complete: "
        f"{result.users_created} users created, "
        f"{result.users_skipped} skipped, "
        f"{result.agents_created} agents created, "
        f"{len(result.errors)} errors"
    )

    if result.group_mapping:
        logger.info("Museum ↔ group mapping (for future collection-access assignment):")
        for entry in result.group_mapping:
            logger.info(f"  {entry['museum']}: {entry['groups']}")

    if result.errors:
        logger.warning(f"{len(result.errors)} errors occurred:")
        for err in result.errors:
            logger.warning(f"  {err}")

    uploaded = _upload_report(result, oracle_env, dry_run)
    if uploaded:
        logger.info("Uploaded report artifacts:")
        for uri in uploaded:
            logger.info(f"  {uri}")

    return {
        "users_created": result.users_created,
        "users_skipped": result.users_skipped,
        "agents_created": result.agents_created,
        "agents_linked": result.agents_linked,
        "errors": result.errors,
        "uploaded": uploaded,
    }
