"""Pull Specify users (Agent + SSO + access) from test MariaDB into staging.

Test is reached through the SSH LocalForward used by ``sync_specify_db_to_test``.
Staging writes go through the Django ORM (same path as ``migrate_users``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from flows.lib.mariadb_logical_sync import (
    MariaDBEndpoint,
    connect_endpoint,
)
from flows.lib.oauth_provider_normalize import canonicalize_provider

logger = logging.getLogger(__name__)

_USER_PRINCIPAL_SUBCLASS = "edu.ku.brc.af.auth.specify.principal.UserPrincipal"


@dataclass
class ExternalIdRow:
    provider: str
    providerid: str
    enabled: bool
    idtoken: Any | None


@dataclass
class PolicyRow:
    collection_id: int | None
    collection_name: str | None
    resource: str
    action: str


@dataclass
class RoleLinkRow:
    role_id: int
    role_name: str
    collection_id: int | None
    collection_name: str | None


@dataclass
class PrincipalLinkRow:
    spprincipal_id: int
    name: str
    groupsubclass: str
    grouptype: str | None


@dataclass
class AgentRow:
    agent_id: int
    agenttype: int
    firstname: str | None
    lastname: str | None
    middleinitial: str | None
    email: str | None
    remarks: str | None
    division_id: int | None
    initials: str | None
    abbreviation: str | None
    title: str | None
    jobtitle: str | None


@dataclass
class TestUserBundle:
    specifyuser_id: int
    name: str
    email: str | None
    password: str
    usertype: str | None
    agent: AgentRow | None
    external_ids: list[ExternalIdRow] = field(default_factory=list)
    policies: list[PolicyRow] = field(default_factory=list)
    roles: list[RoleLinkRow] = field(default_factory=list)
    principals: list[PrincipalLinkRow] = field(default_factory=list)


@dataclass
class UserSyncResult:
    users_created: int = 0
    users_skipped_existing: int = 0
    users_filtered_out: int = 0
    agents_created: int = 0
    sso_created: int = 0
    sso_skipped_existing: int = 0
    policies_created: int = 0
    roles_linked: int = 0
    principals_linked: int = 0
    errors: list[str] = field(default_factory=list)
    planned: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mysql_dict_cursor(conn):
    try:
        import MySQLdb.cursors  # type: ignore

        return conn.cursor(MySQLdb.cursors.DictCursor)
    except ImportError:
        import pymysql.cursors

        return conn.cursor(pymysql.cursors.DictCursor)


def _table_exists(conn, schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
             LIMIT 1
            """,
            [schema, table],
        )
        return cur.fetchone() is not None


def _columns(conn, schema: str, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COLUMN_NAME FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """,
            [schema, table],
        )
        return {str(r[0]) for r in cur.fetchall()}


def _pick_col(cols: set[str], *candidates: str) -> str | None:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _require_col(cols: set[str], table: str, *candidates: str) -> str:
    col = _pick_col(cols, *candidates)
    if not col:
        raise RuntimeError(
            f"Table {table!r} missing expected column among {candidates}"
        )
    return col


def _as_bool(value: Any) -> bool:
    if isinstance(value, bytes):
        value = value[0] if value else 0
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("0", "false", "no", "n", "\x00"):
        return False
    return True


def _parse_idtoken(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return value


def _fetch_collection_names(conn, schema: str) -> dict[int, str]:
    if not _table_exists(conn, schema, "collection"):
        return {}
    cols = _columns(conn, schema, "collection")
    id_col = _pick_col(cols, "CollectionID", "UserGroupScopeId", "UserGroupScopeID")
    name_col = _pick_col(cols, "CollectionName", "Name")
    if not id_col or not name_col:
        return {}
    with _mysql_dict_cursor(conn) as cur:
        cur.execute(f"SELECT `{id_col}` AS id, `{name_col}` AS name FROM collection")
        out: dict[int, str] = {}
        for row in cur.fetchall():
            if row["id"] is None:
                continue
            out[int(row["id"])] = str(row["name"] or "")
        return out


def extract_test_user_bundles(
    test_ep: MariaDBEndpoint,
    *,
    usernames: list[str] | None = None,
) -> list[TestUserBundle]:
    """Load SpecifyUser + linked Agent/SSO/access rows from the test DB."""
    schema = test_ep.database
    with connect_endpoint(test_ep) as conn:
        if not _table_exists(conn, schema, "specifyuser"):
            raise RuntimeError(f"Test DB {schema!r} has no specifyuser table")

        user_cols = _columns(conn, schema, "specifyuser")
        uid_col = _require_col(user_cols, "specifyuser", "SpecifyUserID")
        name_col = _require_col(user_cols, "specifyuser", "Name")
        email_col = _pick_col(user_cols, "EMail", "Email")
        password_col = _require_col(user_cols, "specifyuser", "Password")
        usertype_col = _pick_col(user_cols, "UserType")

        select_parts = [
            f"`{uid_col}` AS specifyuser_id",
            f"`{name_col}` AS name",
            f"`{password_col}` AS password",
        ]
        if email_col:
            select_parts.append(f"`{email_col}` AS email")
        else:
            select_parts.append("NULL AS email")
        if usertype_col:
            select_parts.append(f"`{usertype_col}` AS usertype")
        else:
            select_parts.append("NULL AS usertype")

        sql = f"SELECT {', '.join(select_parts)} FROM specifyuser"
        params: list[Any] = []
        if usernames:
            cleaned = [u.strip() for u in usernames if u and u.strip()]
            if cleaned:
                placeholders = ", ".join(["%s"] * len(cleaned))
                sql += f" WHERE `{name_col}` IN ({placeholders})"
                params.extend(cleaned)
        sql += f" ORDER BY `{name_col}`"

        with _mysql_dict_cursor(conn) as cur:
            cur.execute(sql, params)
            user_rows = list(cur.fetchall())

        collection_names = _fetch_collection_names(conn, schema)
        agents_by_user = _extract_agents(conn, schema, [int(r["specifyuser_id"]) for r in user_rows])
        external_by_user = _extract_external_ids(conn, schema, [int(r["specifyuser_id"]) for r in user_rows])
        policies_by_user = _extract_policies(
            conn, schema, [int(r["specifyuser_id"]) for r in user_rows], collection_names
        )
        roles_by_user = _extract_roles(
            conn, schema, [int(r["specifyuser_id"]) for r in user_rows], collection_names
        )
        principals_by_user = _extract_principals(
            conn, schema, [int(r["specifyuser_id"]) for r in user_rows]
        )

    bundles: list[TestUserBundle] = []
    for row in user_rows:
        uid = int(row["specifyuser_id"])
        bundles.append(
            TestUserBundle(
                specifyuser_id=uid,
                name=str(row["name"] or "").strip(),
                email=(str(row["email"]).strip() if row.get("email") else None) or None,
                password=str(row["password"] or ""),
                usertype=(str(row["usertype"]).strip() if row.get("usertype") else None) or None,
                agent=agents_by_user.get(uid),
                external_ids=external_by_user.get(uid, []),
                policies=policies_by_user.get(uid, []),
                roles=roles_by_user.get(uid, []),
                principals=principals_by_user.get(uid, []),
            )
        )
    return [b for b in bundles if b.name]


def _extract_agents(
    conn, schema: str, user_ids: list[int]
) -> dict[int, AgentRow]:
    if not user_ids or not _table_exists(conn, schema, "agent"):
        return {}
    cols = _columns(conn, schema, "agent")
    agent_id_col = _require_col(cols, "agent", "AgentID")
    user_fk = _require_col(cols, "agent", "SpecifyUserID")
    type_col = _require_col(cols, "agent", "AgentType")

    def col_or_null(name: str, *alts: str) -> str:
        c = _pick_col(cols, name, *alts)
        return f"`{c}` AS {name.lower()}" if c else f"NULL AS {name.lower()}"

    select = ", ".join(
        [
            f"`{agent_id_col}` AS agent_id",
            f"`{user_fk}` AS specifyuser_id",
            f"`{type_col}` AS agenttype",
            col_or_null("FirstName"),
            col_or_null("LastName"),
            col_or_null("MiddleInitial"),
            col_or_null("Email"),
            col_or_null("Remarks"),
            col_or_null("DivisionID"),
            col_or_null("Initials"),
            col_or_null("Abbreviation"),
            col_or_null("Title"),
            col_or_null("JobTitle"),
        ]
    )
    placeholders = ", ".join(["%s"] * len(user_ids))
    with _mysql_dict_cursor(conn) as cur:
        cur.execute(
            f"SELECT {select} FROM agent WHERE `{user_fk}` IN ({placeholders}) "
            f"ORDER BY `{agent_id_col}`",
            user_ids,
        )
        rows = cur.fetchall()

    out: dict[int, AgentRow] = {}
    for row in rows:
        uid = int(row["specifyuser_id"])
        if uid in out:
            continue  # keep first (lowest AgentID)
        out[uid] = AgentRow(
            agent_id=int(row["agent_id"]),
            agenttype=int(row["agenttype"]),
            firstname=row.get("firstname"),
            lastname=row.get("lastname"),
            middleinitial=row.get("middleinitial"),
            email=row.get("email"),
            remarks=row.get("remarks"),
            division_id=int(row["divisionid"]) if row.get("divisionid") is not None else None,
            initials=row.get("initials"),
            abbreviation=row.get("abbreviation"),
            title=row.get("title"),
            jobtitle=row.get("jobtitle"),
        )
    return out


def _extract_external_ids(
    conn, schema: str, user_ids: list[int]
) -> dict[int, list[ExternalIdRow]]:
    if not user_ids or not _table_exists(conn, schema, "spuserexternalid"):
        return {}
    cols = _columns(conn, schema, "spuserexternalid")
    user_fk = _require_col(cols, "spuserexternalid", "specifyuser_id", "SpecifyUserID")
    provider_col = _require_col(cols, "spuserexternalid", "provider")
    providerid_col = _require_col(cols, "spuserexternalid", "providerid")
    enabled_col = _pick_col(cols, "enabled")
    idtoken_col = _pick_col(cols, "idtoken")

    parts = [
        f"`{user_fk}` AS specifyuser_id",
        f"`{provider_col}` AS provider",
        f"`{providerid_col}` AS providerid",
    ]
    parts.append(f"`{enabled_col}` AS enabled" if enabled_col else "1 AS enabled")
    parts.append(f"`{idtoken_col}` AS idtoken" if idtoken_col else "NULL AS idtoken")

    placeholders = ", ".join(["%s"] * len(user_ids))
    with _mysql_dict_cursor(conn) as cur:
        cur.execute(
            f"SELECT {', '.join(parts)} FROM spuserexternalid "
            f"WHERE `{user_fk}` IN ({placeholders})",
            user_ids,
        )
        rows = cur.fetchall()

    out: dict[int, list[ExternalIdRow]] = {}
    for row in rows:
        uid = int(row["specifyuser_id"])
        out.setdefault(uid, []).append(
            ExternalIdRow(
                provider=canonicalize_provider(str(row["provider"] or "")),
                providerid=str(row["providerid"] or ""),
                enabled=_as_bool(row.get("enabled")),
                idtoken=_parse_idtoken(row.get("idtoken")),
            )
        )
    return out


def _extract_policies(
    conn,
    schema: str,
    user_ids: list[int],
    collection_names: dict[int, str],
) -> dict[int, list[PolicyRow]]:
    if not user_ids or not _table_exists(conn, schema, "spuserpolicy"):
        return {}
    cols = _columns(conn, schema, "spuserpolicy")
    user_fk = _require_col(cols, "spuserpolicy", "specifyuser_id", "SpecifyUserID")
    collection_fk = _pick_col(cols, "collection_id", "CollectionID")
    resource_col = _require_col(cols, "spuserpolicy", "resource")
    action_col = _require_col(cols, "spuserpolicy", "action")

    parts = [
        f"`{user_fk}` AS specifyuser_id",
        f"`{resource_col}` AS resource",
        f"`{action_col}` AS action",
    ]
    parts.append(
        f"`{collection_fk}` AS collection_id" if collection_fk else "NULL AS collection_id"
    )
    placeholders = ", ".join(["%s"] * len(user_ids))
    with _mysql_dict_cursor(conn) as cur:
        cur.execute(
            f"SELECT {', '.join(parts)} FROM spuserpolicy "
            f"WHERE `{user_fk}` IN ({placeholders})",
            user_ids,
        )
        rows = cur.fetchall()

    out: dict[int, list[PolicyRow]] = {}
    for row in rows:
        uid = int(row["specifyuser_id"])
        cid = int(row["collection_id"]) if row.get("collection_id") is not None else None
        out.setdefault(uid, []).append(
            PolicyRow(
                collection_id=cid,
                collection_name=collection_names.get(cid) if cid is not None else None,
                resource=str(row["resource"] or ""),
                action=str(row["action"] or ""),
            )
        )
    return out


def _extract_roles(
    conn,
    schema: str,
    user_ids: list[int],
    collection_names: dict[int, str],
) -> dict[int, list[RoleLinkRow]]:
    if (
        not user_ids
        or not _table_exists(conn, schema, "spuserrole")
        or not _table_exists(conn, schema, "sprole")
    ):
        return {}
    ur_cols = _columns(conn, schema, "spuserrole")
    role_cols = _columns(conn, schema, "sprole")
    user_fk = _require_col(ur_cols, "spuserrole", "specifyuser_id", "SpecifyUserID")
    role_fk = _require_col(ur_cols, "spuserrole", "role_id", "RoleID", "sprole_id")
    role_id_col = _require_col(role_cols, "sprole", "id", "SpRoleID", "RoleID")
    role_name_col = _require_col(role_cols, "sprole", "name", "Name")
    role_coll_fk = _pick_col(role_cols, "collection_id", "CollectionID")

    coll_select = (
        f"r.`{role_coll_fk}` AS collection_id" if role_coll_fk else "NULL AS collection_id"
    )
    placeholders = ", ".join(["%s"] * len(user_ids))
    sql = f"""
        SELECT ur.`{user_fk}` AS specifyuser_id,
               r.`{role_id_col}` AS role_id,
               r.`{role_name_col}` AS role_name,
               {coll_select}
          FROM spuserrole ur
          JOIN sprole r ON r.`{role_id_col}` = ur.`{role_fk}`
         WHERE ur.`{user_fk}` IN ({placeholders})
    """
    with _mysql_dict_cursor(conn) as cur:
        cur.execute(sql, user_ids)
        rows = cur.fetchall()

    out: dict[int, list[RoleLinkRow]] = {}
    for row in rows:
        uid = int(row["specifyuser_id"])
        cid = int(row["collection_id"]) if row.get("collection_id") is not None else None
        out.setdefault(uid, []).append(
            RoleLinkRow(
                role_id=int(row["role_id"]),
                role_name=str(row["role_name"] or ""),
                collection_id=cid,
                collection_name=collection_names.get(cid) if cid is not None else None,
            )
        )
    return out


def _extract_principals(
    conn, schema: str, user_ids: list[int]
) -> dict[int, list[PrincipalLinkRow]]:
    if (
        not user_ids
        or not _table_exists(conn, schema, "specifyuser_spprincipal")
        or not _table_exists(conn, schema, "spprincipal")
    ):
        return {}
    link_cols = _columns(conn, schema, "specifyuser_spprincipal")
    p_cols = _columns(conn, schema, "spprincipal")
    user_fk = _require_col(link_cols, "specifyuser_spprincipal", "SpecifyUserID", "specifyuser_id")
    principal_fk = _require_col(
        link_cols, "specifyuser_spprincipal", "SpPrincipalID", "spprincipal_id"
    )
    pid_col = _require_col(p_cols, "spprincipal", "SpPrincipalID")
    name_col = _require_col(p_cols, "spprincipal", "Name")
    subclass_col = _require_col(p_cols, "spprincipal", "GroupSubClass")
    grouptype_col = _pick_col(p_cols, "groupType", "GroupType")

    gt_select = (
        f"p.`{grouptype_col}` AS grouptype" if grouptype_col else "NULL AS grouptype"
    )
    placeholders = ", ".join(["%s"] * len(user_ids))
    sql = f"""
        SELECT l.`{user_fk}` AS specifyuser_id,
               p.`{pid_col}` AS spprincipal_id,
               p.`{name_col}` AS name,
               p.`{subclass_col}` AS groupsubclass,
               {gt_select}
          FROM specifyuser_spprincipal l
          JOIN spprincipal p ON p.`{pid_col}` = l.`{principal_fk}`
         WHERE l.`{user_fk}` IN ({placeholders})
    """
    with _mysql_dict_cursor(conn) as cur:
        cur.execute(sql, user_ids)
        rows = cur.fetchall()

    out: dict[int, list[PrincipalLinkRow]] = {}
    for row in rows:
        subclass = str(row.get("groupsubclass") or "")
        if subclass == _USER_PRINCIPAL_SUBCLASS:
            # Per-user principals are not portable across DBs by ID/name.
            continue
        uid = int(row["specifyuser_id"])
        out.setdefault(uid, []).append(
            PrincipalLinkRow(
                spprincipal_id=int(row["spprincipal_id"]),
                name=str(row["name"] or ""),
                groupsubclass=subclass,
                grouptype=(str(row["grouptype"]) if row.get("grouptype") else None),
            )
        )
    return out


def _staging_existing_usernames() -> set[str]:
    from specifyweb.specify.models import Specifyuser

    return set(Specifyuser.objects.values_list("name", flat=True))


def _resolve_division(division_id: int | None):
    from specifyweb.specify.models import Division

    if division_id is not None:
        div = Division.objects.filter(id=division_id).first()
        if div is not None:
            return div
    div = Division.objects.first()
    if div is None:
        raise RuntimeError("No Division found in staging Specify DB")
    return div


def _collection_id_by_name() -> dict[str, int]:
    from specifyweb.specify.models import Collection

    out: dict[str, int] = {}
    for c in Collection.objects.all():
        name = getattr(c, "collectionname", None) or getattr(c, "name", None) or ""
        if name:
            out[str(name)] = int(c.id)
    return out


def _map_collection_id(
    *,
    collection_id: int | None,
    collection_name: str | None,
    by_name: dict[str, int],
) -> int | None:
    if collection_id is None:
        return None
    from specifyweb.specify.models import Collection

    if Collection.objects.filter(id=collection_id).exists():
        return collection_id
    if collection_name and collection_name in by_name:
        return by_name[collection_name]
    return None


def _resolve_role_id(
    *,
    role_name: str,
    collection_id: int | None,
) -> int | None:
    from specifyweb.backend.permissions.models import Role

    qs = Role.objects.filter(name=role_name)
    if collection_id is None:
        role = qs.first()
    else:
        role = qs.filter(collection_id=collection_id).first() or qs.first()
    return int(role.id) if role is not None else None


def _plan_entry(bundle: TestUserBundle, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "name": bundle.name,
        "test_specifyuser_id": bundle.specifyuser_id,
        "email": bundle.email,
        "usertype": bundle.usertype,
        "has_agent": bundle.agent is not None,
        "external_ids": [
            {"provider": e.provider, "providerid": e.providerid, "enabled": e.enabled}
            for e in bundle.external_ids
        ],
        "policy_count": len(bundle.policies),
        "role_count": len(bundle.roles),
        "principal_count": len(bundle.principals),
    }


def apply_user_bundles_to_staging(
    bundles: list[TestUserBundle],
    *,
    dry_run: bool = True,
    include_policies: bool = True,
    include_principals: bool = True,
    update_existing_sso: bool = True,
) -> UserSyncResult:
    """Create missing staging users from test bundles; optionally backfill SSO."""
    from django.db import transaction
    from specifyweb.specify.models import Agent, Specifyuser, Spprincipal

    result = UserSyncResult()
    existing = _staging_existing_usernames()
    collection_by_name = _collection_id_by_name() if include_policies else {}

    try:
        from specifyweb.backend.accounts.models import Spuserexternalid
    except ImportError as exc:
        raise RuntimeError(
            "Cannot import Spuserexternalid — ensure specify7 submodule is present"
        ) from exc

    try:
        from specifyweb.backend.permissions.models import UserPolicy, UserRole
    except ImportError:
        UserPolicy = None  # type: ignore[assignment]
        UserRole = None  # type: ignore[assignment]
        if include_policies:
            logger.warning("permissions models unavailable; skipping policy/role copy")
            include_policies = False

    for bundle in bundles:
        if not bundle.name:
            continue
        if bundle.name in existing:
            result.users_skipped_existing += 1
            if update_existing_sso and bundle.external_ids:
                try:
                    _sync_sso_for_existing(
                        bundle,
                        result=result,
                        dry_run=dry_run,
                        Spuserexternalid=Spuserexternalid,
                    )
                except Exception as exc:
                    msg = f"SSO backfill failed for existing user {bundle.name!r}: {exc}"
                    logger.error(msg)
                    result.errors.append(msg)
            result.planned.append(_plan_entry(bundle, "skip_existing"))
            continue

        result.planned.append(_plan_entry(bundle, "create"))
        if dry_run:
            result.users_created += 1
            if bundle.agent is not None:
                result.agents_created += 1
            result.sso_created += len(bundle.external_ids)
            if include_policies:
                result.policies_created += len(bundle.policies)
                result.roles_linked += len(bundle.roles)
            if include_principals:
                result.principals_linked += len(bundle.principals)
            continue

        try:
            with transaction.atomic():
                sp_user = Specifyuser(
                    name=bundle.name,
                    email=bundle.email,
                    usertype=bundle.usertype or "Manager",
                    isloggedin=False,
                    isloggedinreport=False,
                    password=bundle.password or "unusable",
                )
                # Bypass set_password hashing — keep test hash / unusable marker as-is.
                sp_user.save()
                result.users_created += 1
                existing.add(bundle.name)

                if bundle.agent is not None:
                    ag = bundle.agent
                    remarks = ag.remarks or ""
                    provenance = f"synced-from-test: SpecifyUserID={bundle.specifyuser_id}"
                    if provenance not in remarks:
                        remarks = f"{remarks}; {provenance}".strip("; ").strip()
                    agent = Agent(
                        agenttype=ag.agenttype,
                        firstname=ag.firstname,
                        lastname=ag.lastname or bundle.name,
                        middleinitial=ag.middleinitial,
                        email=ag.email or bundle.email,
                        remarks=remarks or None,
                        division=_resolve_division(ag.division_id),
                        specifyuser=sp_user,
                    )
                    agent.save()
                    result.agents_created += 1
                else:
                    Agent(
                        agenttype=1,
                        lastname=bundle.name,
                        email=bundle.email,
                        division=_resolve_division(None),
                        specifyuser=sp_user,
                        remarks=f"synced-from-test: SpecifyUserID={bundle.specifyuser_id}",
                    ).save()
                    result.agents_created += 1

                for ext in bundle.external_ids:
                    if not ext.provider or not ext.providerid:
                        continue
                    exists = Spuserexternalid.objects.filter(
                        provider=ext.provider, providerid=ext.providerid
                    ).exists()
                    if exists:
                        result.sso_skipped_existing += 1
                        continue
                    Spuserexternalid.objects.create(
                        provider=ext.provider,
                        providerid=ext.providerid,
                        enabled=ext.enabled,
                        idtoken=ext.idtoken,
                        specifyuser=sp_user,
                    )
                    result.sso_created += 1

                if include_policies and UserPolicy is not None:
                    for pol in bundle.policies:
                        mapped_cid = _map_collection_id(
                            collection_id=pol.collection_id,
                            collection_name=pol.collection_name,
                            by_name=collection_by_name,
                        )
                        if pol.collection_id is not None and mapped_cid is None:
                            result.errors.append(
                                f"{bundle.name}: skip policy "
                                f"{pol.resource}/{pol.action} — collection "
                                f"{pol.collection_id}/{pol.collection_name!r} not on staging"
                            )
                            continue
                        if UserPolicy.objects.filter(
                            specifyuser=sp_user,
                            collection_id=mapped_cid,
                            resource=pol.resource,
                            action=pol.action,
                        ).exists():
                            continue
                        UserPolicy.objects.create(
                            specifyuser=sp_user,
                            collection_id=mapped_cid,
                            resource=pol.resource,
                            action=pol.action,
                        )
                        result.policies_created += 1

                if include_policies and UserRole is not None:
                    for role in bundle.roles:
                        mapped_cid = _map_collection_id(
                            collection_id=role.collection_id,
                            collection_name=role.collection_name,
                            by_name=collection_by_name,
                        )
                        role_id = _resolve_role_id(
                            role_name=role.role_name, collection_id=mapped_cid
                        )
                        if role_id is None:
                            result.errors.append(
                                f"{bundle.name}: skip role {role.role_name!r} — "
                                "no matching sprole on staging"
                            )
                            continue
                        if UserRole.objects.filter(
                            specifyuser=sp_user, role_id=role_id
                        ).exists():
                            continue
                        UserRole.objects.create(specifyuser=sp_user, role_id=role_id)
                        result.roles_linked += 1

                if include_principals:
                    for prin in bundle.principals:
                        qs = Spprincipal.objects.filter(name=prin.name)
                        if prin.groupsubclass:
                            qs = qs.filter(groupsubclass=prin.groupsubclass)
                        target = qs.first()
                        if target is None:
                            result.errors.append(
                                f"{bundle.name}: skip principal {prin.name!r} "
                                f"({prin.groupsubclass}) — not found on staging"
                            )
                            continue
                        sp_user.spprincipals.add(target)
                        result.principals_linked += 1

                logger.info(
                    "Created staging SpecifyUser %r id=%s from test id=%s",
                    bundle.name,
                    sp_user.id,
                    bundle.specifyuser_id,
                )
        except Exception as exc:
            msg = f"Error syncing user {bundle.name!r}: {exc}"
            logger.error(msg)
            result.errors.append(msg)

    return result


def _sync_sso_for_existing(
    bundle: TestUserBundle,
    *,
    result: UserSyncResult,
    dry_run: bool,
    Spuserexternalid: Any,
) -> None:
    from specifyweb.specify.models import Specifyuser

    sp_user = Specifyuser.objects.filter(name=bundle.name).first()
    if sp_user is None:
        return
    for ext in bundle.external_ids:
        if not ext.provider or not ext.providerid:
            continue
        if Spuserexternalid.objects.filter(
            provider=ext.provider, providerid=ext.providerid
        ).exists():
            result.sso_skipped_existing += 1
            continue
        if Spuserexternalid.objects.filter(
            specifyuser=sp_user, provider=ext.provider
        ).exists():
            result.sso_skipped_existing += 1
            continue
        if dry_run:
            result.sso_created += 1
            continue
        Spuserexternalid.objects.create(
            provider=ext.provider,
            providerid=ext.providerid,
            enabled=ext.enabled,
            idtoken=ext.idtoken,
            specifyuser=sp_user,
        )
        result.sso_created += 1
        logger.info(
            "Backfilled SSO %s/%s onto existing staging user %r",
            ext.provider,
            ext.providerid,
            bundle.name,
        )


def sync_users_test_to_staging(
    *,
    dry_run: bool = True,
    usernames: list[str] | None = None,
    include_policies: bool = True,
    include_principals: bool = True,
    update_existing_sso: bool = True,
    test_ep: MariaDBEndpoint,
) -> dict[str, Any]:
    """Extract from ``test_ep`` and apply into the Django-configured staging DB."""
    bundles = extract_test_user_bundles(test_ep, usernames=usernames)
    existing = _staging_existing_usernames()
    only_on_test = [b for b in bundles if b.name not in existing]
    already = [b for b in bundles if b.name in existing]

    result = apply_user_bundles_to_staging(
        bundles,
        dry_run=dry_run,
        include_policies=include_policies,
        include_principals=include_principals,
        update_existing_sso=update_existing_sso,
    )
    return {
        "dry_run": dry_run,
        "test_user_count": len(bundles),
        "only_on_test_count": len(only_on_test),
        "already_on_staging_count": len(already),
        "only_on_test_names": [b.name for b in only_on_test],
        "result": result.as_dict(),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
