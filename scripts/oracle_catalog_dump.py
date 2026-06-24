#!/usr/bin/env python3
"""
Dump ALL Oracle source data associated with a catalog number as a single JSON blob.

Resolves the catalog number to a MUSIT MUSEUM_OBJECT.OBJECT_ID, then walks the
full MUSIT_BOTANIKK_FELLES event/place/taxon/media/note/document graph, collecting
every row from every connected table. Output is a deeply nested JSON written to
stdout (or --output file).

Usage:
    python scripts/oracle_catalog_dump.py --catalog "O-V-123456"
    python scripts/oracle_catalog_dump.py --catalog "TRH-V-241112" --env prod
    python scripts/oracle_catalog_dump.py --catalog 241112
    python scripts/oracle_catalog_dump.py --object-id 12345
    python scripts/oracle_catalog_dump.py --catalog "O-V-123456" --output dump.json

After sourcing port-forward.sh the shell alias also works:
    oracle_catalog_dump --catalog "O-V-123456"

Credentials are read from env vars (ORACLE_PROD_USER / _PASSWORD / _SERVICE etc.)
or from the .env file in the project root.  Requires: source scripts/port-forward.sh.

LOB columns (CLOB/BLOB/NCLOB) are not read by default — only a length placeholder is
stored (no bulk transfer).  Use --include-lobs or ORACLE_CATALOG_INCLUDE_LOBS=1 for a
full read when needed.
"""
from __future__ import annotations

import argparse
import datetime
import decimal
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# .env loading (same pattern as oracle_sql.py)
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key not in os.environ:
                os.environ[key] = val.strip()


_SCRIPTS_DIR = Path(__file__).resolve().parent

# When False (default), LOB columns are not read — only length metadata is used.
_INCLUDE_ORACLE_LOBS = False
_load_dotenv(_SCRIPTS_DIR.parent / ".env")

# ---------------------------------------------------------------------------
# oracledb setup
# ---------------------------------------------------------------------------

try:
    import oracledb
except ImportError:
    sys.exit("oracledb not found. Activate the project venv:\n  source .venv/bin/activate")

_CLIENT_INITIALIZED = False
_current_mode = "unknown"


def _init_oracle_client() -> None:
    global _CLIENT_INITIALIZED, _current_mode
    if _CLIENT_INITIALIZED:
        return
    use_thick = os.getenv("ORACLE_USE_THICK_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not use_thick:
        _CLIENT_INITIALIZED = True
        _current_mode = "thin"
        return
    lib_dir = os.getenv("ORACLE_CLIENT_LIB_DIR") or None
    try:
        if lib_dir:
            oracledb.init_oracle_client(lib_dir=lib_dir)
        else:
            oracledb.init_oracle_client()
        _current_mode = "thick"
    except Exception as exc:
        print(f"Warning: thick-mode init failed — {exc}", file=sys.stderr)
        _current_mode = "thin (fallback)"
    _CLIENT_INITIALIZED = True


_LOCAL_PORTS: dict[str, int] = {"prod": 1553, "test": 1554}


def _connect(env: str):
    prefix = env.upper()
    default_port = _LOCAL_PORTS[env.lower()]
    user = os.environ.get(f"ORACLE_{prefix}_USER")
    password = os.environ.get(f"ORACLE_{prefix}_PASSWORD")
    service = os.environ.get(f"ORACLE_{prefix}_SERVICE")
    host = os.environ.get(f"ORACLE_{prefix}_HOST", "localhost")
    port = int(os.environ.get(f"ORACLE_{prefix}_PORT", str(default_port)))
    missing = [
        k for k, v in {
            f"ORACLE_{prefix}_USER": user,
            f"ORACLE_{prefix}_PASSWORD": password,
            f"ORACLE_{prefix}_SERVICE": service,
        }.items() if not v
    ]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}\n  Export them or add to .env")
    dsn = f"{host}:{port}/{service}"
    return oracledb.connect(user=user, password=password, dsn=dsn)


# ---------------------------------------------------------------------------
# Type coercion and JSON helpers
# ---------------------------------------------------------------------------

def _coerce(v: Any) -> Any:
    """Convert Oracle types to JSON-serialisable Python types."""
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if hasattr(oracledb, "LOB") and isinstance(v, oracledb.LOB):
        if _INCLUDE_ORACLE_LOBS:
            try:
                text = v.read()
                return text if isinstance(text, str) else text.decode("utf-8", errors="replace")
            except Exception as exc:
                return f"<LOB read error: {exc}>"
        try:
            sz = v.size()
        except Exception as exc:
            return f"<LOB size error: {exc}>"
        lob_type = getattr(v, "type", None)
        if lob_type in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
            unit = "chars"
        elif lob_type == oracledb.DB_TYPE_BLOB:
            unit = "bytes"
        else:
            unit = "units"
        return f"<LOB {sz:,} {unit}>"
    if isinstance(v, bytes):
        return f"<BLOB {len(v):,} bytes>"
    if isinstance(v, decimal.Decimal):
        # Preserve integer-looking NUMBER values as ints; else float for JSON.
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {str(k): _coerce(val) for k, val in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_coerce(item) for item in v]
    if hasattr(oracledb, "DbObject") and isinstance(v, oracledb.DbObject):
        try:
            if getattr(v.type, "iscollection", False):
                return [_coerce(item) for item in v.aslist()]
            attrs = {}
            for attr in getattr(v.type, "attributes", []):
                name = attr.name
                attrs[name.lower()] = _coerce(getattr(v, name))
            return attrs
        except Exception as exc:
            return f"<DbObject serialisation error: {exc}>"
    if isinstance(v, float) and (v != v):  # NaN
        return None
    return v


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        return _coerce(o)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _query(con, sql: str, params: dict | None = None) -> list[dict]:
    """Execute SQL and return rows as list of dicts (lowercase column names)."""
    cur = con.cursor()
    try:
        cur.execute(sql, params or {})
        if cur.description is None:
            return []
        cols = [d[0].lower() for d in cur.description]
        return [
            {cols[i]: _coerce(v) for i, v in enumerate(row)}
            for row in cur.fetchall()
        ]
    finally:
        cur.close()


# ORA codes that mean "not accessible / not found" — log and continue
_SKIP_ORA_CODES = {
    942,   # table or view does not exist
    1031,  # insufficient privileges
    904,   # invalid column name (view column mismatch)
    12704, # character set mismatch
}


def _try_query(
    con, sql: str, params: dict | None = None, label: str = ""
) -> list[dict]:
    """_query() but returns [] on ORA errors that indicate missing/inaccessible tables."""
    try:
        return _query(con, sql, params)
    except oracledb.DatabaseError as exc:
        code = exc.args[0].code if exc.args else 0
        if code in _SKIP_ORA_CODES:
            print(
                f"  [skip] {label or sql[:80]!r}  ORA-{code:05d}",
                file=sys.stderr,
            )
            return []
        raise


def _in_list(ids: list) -> str:
    """Build an SQL IN list literal from a list of integer-like IDs.

    Raises ValueError if the list is empty — callers must check before calling.
    Oracle limits IN lists to 1000 items; specimens typically have far fewer linked rows.
    """
    if not ids:
        raise ValueError("_in_list called with empty list")
    return "(" + ", ".join(str(int(i)) for i in ids) + ")"


# ---------------------------------------------------------------------------
# Schema constant
# ---------------------------------------------------------------------------

S = "MUSIT_BOTANIKK_FELLES"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _fetch_object_core(con, object_id: int) -> dict:
    """MUSEUM_OBJECT plus all tables with a direct OBJECT_ID FK."""
    oid = object_id
    result: dict[str, Any] = {}

    rows = _try_query(con, f"SELECT * FROM {S}.MUSEUM_OBJECT WHERE OBJECT_ID = :oid", {"oid": oid}, "MUSEUM_OBJECT")
    result["MUSEUM_OBJECT"] = rows[0] if rows else None

    rows = _try_query(con, f"SELECT * FROM {S}.OBJECT_ATTRIBUTES WHERE OBJECT_ID = :oid", {"oid": oid}, "OBJECT_ATTRIBUTES")
    result["OBJECT_ATTRIBUTES"] = rows[0] if rows else None

    rows = _try_query(con, f"SELECT * FROM {S}.V_OBJECT_ATTRIBUTES WHERE OBJECT_ID = :oid", {"oid": oid}, "V_OBJECT_ATTRIBUTES")
    result["V_OBJECT_ATTRIBUTES"] = rows[0] if rows else None

    rows = _try_query(con, f"SELECT * FROM {S}.V_COUNT_PHOTO WHERE OBJECT_ID = :oid", {"oid": oid}, "V_COUNT_PHOTO")
    result["V_COUNT_PHOTO"] = rows[0] if rows else None

    rows = _try_query(
        con,
        f"SELECT * FROM {S}.OBJECT_HIERARCHY WHERE OBJECT_ID = :oid OR PARENT_OBJECT_ID = :oid",
        {"oid": oid},
        "OBJECT_HIERARCHY",
    )
    result["OBJECT_HIERARCHY"] = rows

    rows = _try_query(
        con, f"SELECT * FROM {S}.MUSEUM_OBJECT_LEGNR_PERSON WHERE OBJECT_ID = :oid", {"oid": oid},
        "MUSEUM_OBJECT_LEGNR_PERSON",
    )
    result["MUSEUM_OBJECT_LEGNR_PERSON"] = rows

    # Classification history chain for this object
    rows = _try_query(
        con, f"SELECT * FROM {S}.CLASS_HIST_FOR_OBJECT WHERE OBJECT_ID = :oid ORDER BY SEQ_NO", {"oid": oid},
        "CLASS_HIST_FOR_OBJECT",
    )
    result["CLASS_HIST_FOR_OBJECT"] = rows

    # DNA sampling events directly tied to the object (bridge table)
    rows = _try_query(
        con, f"SELECT * FROM {S}.MUSEUM_OBJECT_DNA_EVENT WHERE OBJECT_ID = :oid", {"oid": oid},
        "MUSEUM_OBJECT_DNA_EVENT",
    )
    result["MUSEUM_OBJECT_DNA_EVENT"] = rows

    # Lending records associated with the object
    rows = _try_query(
        con, f"SELECT * FROM {S}.LENDING_OBJECTS WHERE OBJECT_ID = :oid", {"oid": oid},
        "LENDING_OBJECTS",
    )
    result["LENDING_OBJECTS"] = rows

    # Object–place associations outside the event graph
    por_rows = _try_query(
        con, f"SELECT * FROM {S}.PLACE_OBJECT_ROLE WHERE OBJECT_ID = :oid", {"oid": oid},
        "PLACE_OBJECT_ROLE",
    )
    if por_rows:
        place_ids = list({r["place_id"] for r in por_rows if r.get("place_id")})
        por_places: dict[int, dict] = {}
        if place_ids:
            por_places = _fetch_places(con, place_ids)
        result["PLACE_OBJECT_ROLE"] = [
            {**r, "_PLACE_DATA": por_places.get(r.get("place_id"))}
            for r in por_rows
        ]
    else:
        result["PLACE_OBJECT_ROLE"] = []

    return result


def _fetch_places(con, place_ids: list[int]) -> dict[int, dict]:
    """Walk every place facet table for a set of PLACE_IDs."""
    if not place_ids:
        return {}
    in_c = _in_list(place_ids)
    places: dict[int, dict] = {}

    # PLACE
    for r in _try_query(con, f"SELECT * FROM {S}.PLACE WHERE PLACE_ID IN {in_c}", label="PLACE"):
        places[r["place_id"]] = {"PLACE": r}

    # PLACE_LOCALITY_PLACE → LOCALITY_PLACE
    plp_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_LOCALITY_PLACE WHERE PLACE_ID IN {in_c}", label="PLACE_LOCALITY_PLACE")
    lp_ids = [r["locality_place_id"] for r in plp_rows if r.get("locality_place_id")]
    lp_by_id: dict = {}
    if lp_ids:
        lp_rows = _try_query(con, f"SELECT * FROM {S}.LOCALITY_PLACE WHERE LOCALITY_PLACE_ID IN {_in_list(lp_ids)}", label="LOCALITY_PLACE")
        lp_by_id = {r["locality_place_id"]: r for r in lp_rows}
    for r in plp_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("LOCALITY_PLACE", []).append(
                {**r, "_LOCALITY_PLACE": lp_by_id.get(r.get("locality_place_id"))}
            )

    # PLACE_HIERACHICAL_PLACE → HIERARCHICAL_PLACE_OLD
    # Note: junction column is HIERACHICAL_PLACE_ID (typo in Oracle); PK in OLD table is HIERARCH_PLACE_ID
    php_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_HIERACHICAL_PLACE WHERE PLACE_ID IN {in_c}", label="PLACE_HIERACHICAL_PLACE")
    hier_ids = [r["hierachical_place_id"] for r in php_rows if r.get("hierachical_place_id")]
    hier_by_id: dict = {}
    if hier_ids:
        h_rows = _try_query(con, f"SELECT * FROM {S}.HIERARCHICAL_PLACE_OLD WHERE HIERARCH_PLACE_ID IN {_in_list(hier_ids)}", label="HIERARCHICAL_PLACE_OLD")
        hier_by_id = {r["hierarch_place_id"]: r for r in h_rows}
    for r in php_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("HIERARCHICAL_PLACE", []).append(
                {**r, "_HIERARCHICAL_PLACE_OLD": hier_by_id.get(r.get("hierachical_place_id"))}
            )

    # KOORDINATE_PLACE_PLACE → KOORDINATE_PLACE → DERIVED_COORDINATES + POINTS
    kpp_rows = _try_query(con, f"SELECT * FROM {S}.KOORDINATE_PLACE_PLACE WHERE PLACE_ID IN {in_c}", label="KOORDINATE_PLACE_PLACE")
    kp_ids = [r["koordinate_place_id"] for r in kpp_rows if r.get("koordinate_place_id")]
    kp_by_id: dict = {}
    derived_by_kp: dict[int, list] = {}
    points_by_kp: dict[int, list] = {}
    if kp_ids:
        kp_in = _in_list(kp_ids)
        kp_rows = _try_query(con, f"SELECT * FROM {S}.KOORDINATE_PLACE WHERE KOORDINATE_PLACE_ID IN {kp_in}", label="KOORDINATE_PLACE")
        kp_by_id = {r["koordinate_place_id"]: r for r in kp_rows}
        for r in _try_query(con, f"SELECT * FROM {S}.DERIVED_COORDINATES WHERE KOORDINATE_PLACE_ID IN {kp_in}", label="DERIVED_COORDINATES"):
            derived_by_kp.setdefault(r["koordinate_place_id"], []).append(r)
        # Extra geometry points attached to coordinate places
        for r in _try_query(con, f"SELECT * FROM {S}.POINTS WHERE KOORDINATE_PLACE_ID IN {kp_in} ORDER BY SEQUENCE_NUMBER", label="POINTS"):
            points_by_kp.setdefault(r["koordinate_place_id"], []).append(r)
    for r in kpp_rows:
        if r["place_id"] in places:
            kpid = r.get("koordinate_place_id")
            entry: dict = {**r, "_KOORDINATE_PLACE": kp_by_id.get(kpid)}
            if kpid and kpid in derived_by_kp:
                entry["_DERIVED_COORDINATES"] = derived_by_kp[kpid]
            if kpid and kpid in points_by_kp:
                entry["_POINTS"] = points_by_kp[kpid]
            places[r["place_id"]].setdefault("KOORDINATE_PLACE", []).append(entry)

    # PLACE_ADMINISTRATIVE_PLACE → ADMINISTRATIVE_PLACE
    pap_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_ADMINISTRATIVE_PLACE WHERE PLACE_ID IN {in_c}", label="PLACE_ADMINISTRATIVE_PLACE")
    ap_ids = [r["administrative_place_id"] for r in pap_rows if r.get("administrative_place_id")]
    ap_by_id: dict = {}
    if ap_ids:
        ap_rows = _try_query(con, f"SELECT * FROM {S}.ADMINISTRATIVE_PLACE WHERE ADMINISTRATIVE_PLACE_ID IN {_in_list(ap_ids)}", label="ADMINISTRATIVE_PLACE")
        ap_by_id = {r["administrative_place_id"]: r for r in ap_rows}
    for r in pap_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("ADMINISTRATIVE_PLACE", []).append(
                {**r, "_ADMINISTRATIVE_PLACE": ap_by_id.get(r.get("administrative_place_id"))}
            )

    # PLACE_BIO_GEOGRAFISK_REGION → MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION
    pbgr_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_BIO_GEOGRAFISK_REGION WHERE PLACE_ID IN {in_c}", label="PLACE_BIO_GEOGRAFISK_REGION")
    bgr_ids = [r["bio_geografisk_region_id"] for r in pbgr_rows if r.get("bio_geografisk_region_id")]
    bgr_by_id: dict = {}
    if bgr_ids:
        bgr_rows = _try_query(
            con,
            "SELECT * FROM MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION WHERE BIO_GEO_REG_ID IN " + _in_list(bgr_ids),
            label="MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION",
        )
        bgr_by_id = {r["bio_geo_reg_id"]: r for r in bgr_rows}
    for r in pbgr_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("BIO_GEOGRAFISK_REGION", []).append(
                {**r, "_BIO_GEOGRAFISK_REGION": bgr_by_id.get(r.get("bio_geografisk_region_id"))}
            )

    # PLACE_INDEXED_LOCALITY → INDEXED_LOCALITY
    pil_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_INDEXED_LOCALITY WHERE PLACE_ID IN {in_c}", label="PLACE_INDEXED_LOCALITY")
    il_ids = [r["indexed_locality_id"] for r in pil_rows if r.get("indexed_locality_id")]
    il_by_id: dict = {}
    if il_ids:
        il_rows = _try_query(con, f"SELECT * FROM {S}.INDEXED_LOCALITY WHERE INDEXED_LOCALITY_ID IN {_in_list(il_ids)}", label="INDEXED_LOCALITY")
        il_by_id = {r["indexed_locality_id"]: r for r in il_rows}
    for r in pil_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("INDEXED_LOCALITY", []).append(
                {**r, "_INDEXED_LOCALITY": il_by_id.get(r.get("indexed_locality_id"))}
            )

    # PLACE_ECOLOGY_PLACE → ECOLOGY_PLACE
    pep_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_ECOLOGY_PLACE WHERE PLACE_ID IN {in_c}", label="PLACE_ECOLOGY_PLACE")
    ep_ids = [r.get("ecology_place_id") for r in pep_rows if r.get("ecology_place_id")]
    ep_by_id: dict = {}
    if ep_ids:
        ep_rows = _try_query(con, f"SELECT * FROM {S}.ECOLOGY_PLACE WHERE ECOLOGY_PLACE_ID IN {_in_list(ep_ids)}", label="ECOLOGY_PLACE")
        ep_by_id = {r["ecology_place_id"]: r for r in ep_rows}
    for r in pep_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("ECOLOGY_PLACE", []).append(
                {**r, "_ECOLOGY_PLACE": ep_by_id.get(r.get("ecology_place_id"))}
            )

    # PLACE_STORING_PLACE → STORING_PLACE
    psp_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_STORING_PLACE WHERE PLACE_ID IN {in_c}", label="PLACE_STORING_PLACE")
    sp_ids = [r.get("storing_place_id") for r in psp_rows if r.get("storing_place_id")]
    sp_by_id: dict = {}
    if sp_ids:
        sp_rows = _try_query(con, f"SELECT * FROM {S}.STORING_PLACE WHERE STORING_PLACE_ID IN {_in_list(sp_ids)}", label="STORING_PLACE")
        sp_by_id = {r["storing_place_id"]: r for r in sp_rows}
    for r in psp_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("STORING_PLACE", []).append(
                {**r, "_STORING_PLACE": sp_by_id.get(r.get("storing_place_id"))}
            )

    # PLACE_GENERIC_PLACE_DESC → GENERIC_PLACE_DESCRIPTION (long-text locality descriptions)
    pgpd_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_GENERIC_PLACE_DESC WHERE PLACE_ID IN {in_c}", label="PLACE_GENERIC_PLACE_DESC")
    gpd_ids = [r.get("gen_placedesc_id") for r in pgpd_rows if r.get("gen_placedesc_id")]
    gpd_by_id: dict = {}
    if gpd_ids:
        gpd_rows = _try_query(con, f"SELECT * FROM {S}.GENERIC_PLACE_DESCRIPTION WHERE GEN_PLACEDESC_ID IN {_in_list(gpd_ids)}", label="GENERIC_PLACE_DESCRIPTION")
        gpd_by_id = {r["gen_placedesc_id"]: r for r in gpd_rows}
    for r in pgpd_rows:
        if r["place_id"] in places:
            places[r["place_id"]].setdefault("GENERIC_PLACE_DESCRIPTION", []).append(
                {**r, "_GENERIC_PLACE_DESCRIPTION": gpd_by_id.get(r.get("gen_placedesc_id"))}
            )

    # PLACE_HIEARCHY — parent/child place graph
    ph_rows = _try_query(
        con,
        f"SELECT * FROM {S}.PLACE_HIEARCHY WHERE PARENT_PLACE_ID IN {in_c} OR CHILD_PLACE_ID IN {in_c}",
        label="PLACE_HIEARCHY",
    )
    for pid in place_ids:
        related = [r for r in ph_rows if r.get("parent_place_id") == pid or r.get("child_place_id") == pid]
        if related and pid in places:
            places[pid]["PLACE_HIEARCHY"] = related

    return places


def _fetch_taxonomy(con, class_term_id: int) -> dict:
    """CLASSIFICATION_TERM → CLASSTERM_LATIN_NAME → LATIN_NAMES + vocabulary, and TAXON branch."""
    result: dict[str, Any] = {}

    rows = _try_query(con, f"SELECT * FROM {S}.CLASSIFICATION_TERM WHERE CLASS_TERM_ID = :ctid", {"ctid": class_term_id}, "CLASSIFICATION_TERM")
    result["CLASSIFICATION_TERM"] = rows[0] if rows else None

    # CLASSTERM_LATIN_NAME links terms to formal latin names.
    # Try both 'classterm_id' and 'class_term_id' as different versions of MUSIT use different underscores.
    ctl_rows = _try_query(con, f"SELECT * FROM {S}.CLASSTERM_LATIN_NAME WHERE CLASSTERM_ID = :ctid", {"ctid": class_term_id}, "CLASSTERM_LATIN_NAME")
    if not ctl_rows:
        ctl_rows = _try_query(con, f"SELECT * FROM {S}.CLASSTERM_LATIN_NAME WHERE CLASS_TERM_ID = :ctid", {"ctid": class_term_id}, "CLASSTERM_LATIN_NAME (fallback)")
    result["CLASSTERM_LATIN_NAME"] = ctl_rows
 
    ln_ids = [r["latin_name_id"] for r in ctl_rows if r.get("latin_name_id")]

    # Cross-links between latin names (synonyms, hybrids etc.)
    if not ln_ids:
        # Heuristic fallback: if no formal link, try to find by string match on valid_classterm
        ct = result.get("CLASSIFICATION_TERM") or {}
        vct = ct.get("valid_classterm")
        if vct:
            # Try exact match first
            name_clean = vct.strip()
            ln_fallback = _try_query(con, f"SELECT * FROM {S}.LATIN_NAMES WHERE FULL_NAME_AUTHOR = :name OR FULL_NAME = :name", {"name": name_clean}, "LATIN_NAMES (exact fallback)")
            
            if not ln_fallback:
                # Try normalization fallback: strip spaces and dots
                # Note: This is a bit slow on Oracle but fine for single-object dumps.
                ln_fallback = _try_query(
                    con, 
                    f"SELECT * FROM {S}.LATIN_NAMES WHERE LOWER(REPLACE(REPLACE(FULL_NAME_AUTHOR, ' ', ''), '.', '')) = LOWER(REPLACE(REPLACE(:name, ' ', ''), '.', ''))", 
                    {"name": name_clean}, 
                    "LATIN_NAMES (fuzzy fallback)"
                )
            
            if ln_fallback:
                result["LATIN_NAMES"] = ln_fallback
                ln_ids = [r["latin_name_id"] for r in ln_fallback]

    if ln_ids:
        if "LATIN_NAMES" not in result:
            ln_rows = _try_query(con, f"SELECT * FROM {S}.LATIN_NAMES WHERE LATIN_NAME_ID IN {_in_list(ln_ids)}", label="LATIN_NAMES")
            result["LATIN_NAMES"] = ln_rows
        else:
            ln_rows = result["LATIN_NAMES"]

        # Walk up the tree to get full hierarchy
        all_ln_rows = list(ln_rows)
        seen_ln_ids = set(ln_ids)
        to_resolve = [r["parent_latin_name_id"] for r in ln_rows if r.get("parent_latin_name_id") and r["parent_latin_name_id"] not in seen_ln_ids]
        
        while to_resolve:
            p_rows = _try_query(con, f"SELECT * FROM {S}.LATIN_NAMES WHERE LATIN_NAME_ID IN {_in_list(to_resolve)}", label="LATIN_NAMES (parent walk)")
            for pr in p_rows:
                all_ln_rows.append(pr)
                seen_ln_ids.add(pr["latin_name_id"])
            
            to_resolve = [r["parent_latin_name_id"] for r in p_rows if r.get("parent_latin_name_id") and r["parent_latin_name_id"] not in seen_ln_ids]
        
        result["LATIN_NAMES"] = all_ln_rows
        ln_rows = all_ln_rows

        # Resolve AUTHORSTRINGS for each latin name's author_id
        author_ids = list({r["author_id"] for r in ln_rows if r.get("author_id")})
        if author_ids:
            a_rows = _try_query(con, f"SELECT * FROM {S}.AUTHORSTRINGS WHERE AUTHOR_ID IN {_in_list(author_ids)}", label="AUTHORSTRINGS")
            result["AUTHORSTRINGS"] = {r["author_id"]: r for r in a_rows}

        # Resolve TAXON_CATHEGORY for rank/category codes
        tc_ids = list({r["tax_cath_id"] for r in ln_rows if r.get("tax_cath_id")})
        # Also pick up taxon_cathegory_id from CLASSTERM_LATIN_NAME rows
        tc_ids += [r["taxon_cathegory_id"] for r in ctl_rows if r.get("taxon_cathegory_id") and r["taxon_cathegory_id"] not in tc_ids]
        tc_ids_uniq = list(dict.fromkeys(tc_ids))
        if tc_ids_uniq:
            tc_rows = _try_query(con, f"SELECT * FROM {S}.TAXON_CATHEGORY WHERE TAX_CATH_ID IN {_in_list(tc_ids_uniq)}", label="TAXON_CATHEGORY")
            result["TAXON_CATHEGORY"] = {r["tax_cath_id"]: r for r in tc_rows}

        # INFRASPES_RANK from CLASSIFICATION_TERM also references TAXON_CATHEGORY
        ct = result.get("CLASSIFICATION_TERM") or {}
        ir_id = ct.get("infraspes_rank")
        if ir_id and isinstance(ir_id, int):
            existing = result.get("TAXON_CATHEGORY", {})
            if ir_id not in existing:
                ir_rows = _try_query(con, f"SELECT * FROM {S}.TAXON_CATHEGORY WHERE TAX_CATH_ID = :id", {"id": ir_id}, "TAXON_CATHEGORY (infraspes_rank)")
                if ir_rows:
                    existing[ir_id] = ir_rows[0]
                    result["TAXON_CATHEGORY"] = existing

        # Cross-links between latin names (synonyms, hybrids etc.)
        rel_rows = _try_query(
            con,
            f"SELECT * FROM {S}.LATIN_NAME_RELATIONS WHERE LATIN_NAME_ID1 IN {_in_list(ln_ids)} OR LATIN_NAME_ID2 IN {_in_list(ln_ids)}",
            label="LATIN_NAME_RELATIONS",
        )
        if rel_rows:
            result["LATIN_NAME_RELATIONS"] = rel_rows

        # Hybrid entries pointing at these names
        hyb_rows = _try_query(
            con,
            f"SELECT * FROM {S}.HYBRID_LATIN_NAME WHERE LATIN_NAME_ID IN {_in_list(ln_ids)}",
            label="HYBRID_LATIN_NAME",
        )
        if hyb_rows:
            h_ids = list({r["hybrid_id"] for r in hyb_rows if r.get("hybrid_id")})
            hc_rows = _try_query(con, f"SELECT * FROM {S}.HYBRID_COMBINATIONS WHERE HYBRID_ID IN {_in_list(h_ids)}", label="HYBRID_COMBINATIONS") if h_ids else []
            result["HYBRID_LATIN_NAME"] = hyb_rows
            if hc_rows:
                result["HYBRID_COMBINATIONS"] = hc_rows

        # Fungus group names
        fg_rows = _try_query(con, f"SELECT * FROM {S}.LATIN_NAME_FUNGUS_GROUP WHERE LATIN_NAME_ID IN {_in_list(ln_ids)}", label="LATIN_NAME_FUNGUS_GROUP")
        if fg_rows:
            result["LATIN_NAME_FUNGUS_GROUP"] = fg_rows

        adb_taxon_ids = [r["adb_taxon_id"] for r in ln_rows if r.get("adb_taxon_id")]
        nhm_taxon_ids = [r["nhm_taxon_id"] for r in ln_rows if r.get("nhm_taxon_id")]
        result["_adb_taxon_ids"] = adb_taxon_ids
        result["_nhm_taxon_ids"] = nhm_taxon_ids

    # CLASSIFICATION_TAXON → TAXON (alternative taxon-graph path)
    ctx_rows = _try_query(con, f"SELECT * FROM {S}.CLASSIFICATION_TAXON WHERE CLASS_TERM_ID = :ctid", {"ctid": class_term_id}, "CLASSIFICATION_TAXON")
    if ctx_rows:
        result["CLASSIFICATION_TAXON"] = ctx_rows
        t_ids = [r["tax_id"] for r in ctx_rows if r.get("tax_id")]
        if t_ids:
            t_rows = _try_query(con, f"SELECT * FROM {S}.TAXON WHERE TAXON_ID IN {_in_list(t_ids)}", label="TAXON")
            result["TAXON"] = t_rows
            # Resolve VALID_LATIN_NAME_ID for each taxon (may differ from classterm path)
            extra_ln_ids = [r["valid_latin_name_id"] for r in t_rows if r.get("valid_latin_name_id")]
            if extra_ln_ids:
                extra_ln_rows = _try_query(con, f"SELECT * FROM {S}.LATIN_NAMES WHERE LATIN_NAME_ID IN {_in_list(extra_ln_ids)}", label="LATIN_NAMES (taxon.valid)")
                existing_lns = {r["latin_name_id"] for r in result.get("LATIN_NAMES", [])}
                new_lns = [r for r in extra_ln_rows if r["latin_name_id"] not in existing_lns]
                if new_lns:
                    result.setdefault("LATIN_NAMES", []).extend(new_lns)

    return result


def _fetch_events(con, object_id: int) -> list[dict]:
    """Walk the full event graph for this OBJECT_ID."""
    oid = object_id

    emo_rows = _try_query(
        con,
        f"SELECT * FROM {S}.EVENT_MUSEUM_OBJECT WHERE OBJECT_ID = :oid ORDER BY SEQUENCE_NUMBER",
        {"oid": oid},
        "EVENT_MUSEUM_OBJECT",
    )
    if not emo_rows:
        return []

    event_ids = [r["event_id"] for r in emo_rows if r.get("event_id") is not None]
    if not event_ids:
        return []
    in_c = _in_list(event_ids)

    # ---- Base EVENT and TIMESPAN ----
    events_rows = _try_query(con, f"SELECT * FROM {S}.EVENT WHERE EVENT_ID IN {in_c}", label="EVENT")
    events_by_id = {r["event_id"]: r for r in events_rows}

    ts_ids = list({r["timespan_id"] for r in events_rows if r.get("timespan_id")})
    timespans_by_id: dict = {}
    if ts_ids:
        ts_rows = _try_query(con, f"SELECT * FROM {S}.TIMESPAN WHERE TIMESPAN_ID IN {_in_list(ts_ids)}", label="TIMESPAN")
        timespans_by_id = {r["timespan_id"]: r for r in ts_rows}

    # ---- EVENT_HIERARCHY: links between events ----
    eh_rows = _try_query(
        con,
        f"SELECT * FROM {S}.EVENT_HIERARCHY WHERE PARENT_EVENT_ID IN {in_c} OR CHILD_EVENT_ID IN {in_c}",
        label="EVENT_HIERARCHY",
    )
    eh_by_event: dict[int, list] = {}
    for r in eh_rows:
        for key in ("parent_event_id", "child_event_id"):
            eid = r.get(key)
            if eid in events_by_id:
                eh_by_event.setdefault(eid, []).append(r)

    # ---- Type-specific event subtables (each shares EVENT_ID as PK / FK) ----
    event_subtables = [
        "COLLECTING_EVENT",
        "CLASSIFICATION_EVENT",
        "TYPIFICATION_EVENT",
        "CONSERVATION_EVENT",
        "CONDITION_ASSESSMENT_EVENT",
        "LENDING_EVENT",
        "MOVING_EVENT",
        "OBSERVATION_EVENT",
        "DNA_SAMPLING_EVENT",
        "MEASURMENT_EVENT",
        "DATABASE_EVENT",
        "IDENTIFIER_ASSIGNMENT",
        "LEGACY_EVENT",
    ]
    typed_by_table: dict[str, dict[int, dict]] = {}
    for tbl in event_subtables:
        rows = _try_query(con, f"SELECT * FROM {S}.{tbl} WHERE EVENT_ID IN {in_c}", label=tbl)
        typed_by_table[tbl] = {r["event_id"]: r for r in rows}

    # MEASURMENT child rows (actual measurement values under MEASURMENT_EVENT headers)
    measurment_event_ids = list(typed_by_table.get("MEASURMENT_EVENT", {}).keys())
    measurments_by_event: dict[int, list] = {}
    if measurment_event_ids:
        m_rows = _try_query(
            con,
            f"SELECT * FROM {S}.MEASURMENT WHERE MEASURMENT_EVENT_ID IN {_in_list(measurment_event_ids)}",
            label="MEASURMENT",
        )
        for r in m_rows:
            measurments_by_event.setdefault(r["measurment_event_id"], []).append(r)

    # PLACE_REVISION: coordinate-change audit trail per event
    pr_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_REVISION WHERE EVENT_ID IN {in_c}", label="PLACE_REVISION")
    pr_by_event: dict[int, list] = {}
    for r in pr_rows:
        pr_by_event.setdefault(r["event_id"], []).append(r)

    # ---- People / agents per event ----
    # EVENT_ROLE_PERSON_NAME + PERSON_NAME
    erpn_rows = _try_query(con, f"SELECT * FROM {S}.EVENT_ROLE_PERSON_NAME WHERE EVENT_ID IN {in_c}", label="EVENT_ROLE_PERSON_NAME")
    erpn_by_event: dict[int, list] = {}
    for r in erpn_rows:
        erpn_by_event.setdefault(r["event_id"], []).append(r)

    pn_ids = list({r["person_name_id"] for r in erpn_rows if r.get("person_name_id")})
    pn_by_id: dict = {}
    if pn_ids:
        pn_rows = _try_query(con, f"SELECT * FROM {S}.PERSON_NAME WHERE PERSON_NAME_ID IN {_in_list(pn_ids)}", label="PERSON_NAME")
        pn_by_id = {r["person_name_id"]: r for r in pn_rows}

    # EVENT_ROLE_ACTOR + ACTOR + PERSON_NAME (for actor.valid_person_name_id)
    era_rows = _try_query(con, f"SELECT * FROM {S}.EVENT_ROLE_ACTOR WHERE EVENT_ID IN {in_c}", label="EVENT_ROLE_ACTOR")
    era_by_event: dict[int, list] = {}
    for r in era_rows:
        era_by_event.setdefault(r["event_id"], []).append(r)

    actor_ids = list({r["actor_id"] for r in era_rows if r.get("actor_id")})
    actors_by_id: dict = {}
    groupmemberships_by_actor: dict[int, list] = {}
    if actor_ids:
        a_rows = _try_query(con, f"SELECT * FROM {S}.ACTOR WHERE ACTOR_ID IN {_in_list(actor_ids)}", label="ACTOR")
        actors_by_id = {r["actor_id"]: r for r in a_rows}

        # fetch PERSON_NAME for actors that reference one
        extra_pn_ids = [
            r["valid_person_name_id"] for r in a_rows
            if r.get("valid_person_name_id") and r["valid_person_name_id"] not in pn_by_id
        ]
        if extra_pn_ids:
            extra_pn_rows = _try_query(
                con,
                f"SELECT * FROM {S}.PERSON_NAME WHERE PERSON_NAME_ID IN {_in_list(extra_pn_ids)}",
                label="PERSON_NAME (actor)",
            )
            for r in extra_pn_rows:
                pn_by_id[r["person_name_id"]] = r

        # GROUPMEMBERSHIP: resolve institutional/group actor memberships
        gm_rows = _try_query(
            con,
            f"SELECT * FROM {S}.GROUPMEMBERSHIP WHERE GROUP_ID IN {_in_list(actor_ids)} OR MEMBER_ID IN {_in_list(actor_ids)}",
            label="GROUPMEMBERSHIP",
        )
        for r in gm_rows:
            for key in ("group_id", "member_id"):
                aid = r.get(key)
                if aid in actors_by_id:
                    groupmemberships_by_actor.setdefault(aid, []).append(r)

    # ---- ROLES vocabulary (decode role_id values) ----
    role_ids = list(
        {r.get("role_id") for r in erpn_rows + era_rows if r.get("role_id")}
    )
    roles_by_id: dict = {}
    if role_ids:
        r_rows = _try_query(con, f"SELECT * FROM {S}.ROLES WHERE ROLE_ID IN {_in_list(role_ids)}", label="ROLES")
        roles_by_id = {r["role_id"]: r for r in r_rows}

    # ---- Notes per event ----
    en_rows = _try_query(con, f"SELECT * FROM {S}.EVENT_NOTE WHERE EVENT_ID IN {in_c}", label="EVENT_NOTE")
    en_by_event: dict[int, list] = {}
    for r in en_rows:
        en_by_event.setdefault(r["event_id"], []).append(r)

    en_note_ids = list({r["note_id"] for r in en_rows if r.get("note_id")})
    notes_by_id: dict = {}
    if en_note_ids:
        n_rows = _try_query(con, f"SELECT * FROM {S}.NOTE WHERE NOTE_ID IN {_in_list(en_note_ids)}", label="NOTE")
        notes_by_id = {r["note_id"]: r for r in n_rows}

    # ---- Documents per event ----
    ed_rows = _try_query(con, f"SELECT * FROM {S}.EVENT_DOCUMENT WHERE EVENT_ID IN {in_c}", label="EVENT_DOCUMENT")
    ed_by_event: dict[int, list] = {}
    for r in ed_rows:
        ed_by_event.setdefault(r["event_id"], []).append(r)

    ed_doc_ids = list({r["document_id"] for r in ed_rows if r.get("document_id")})
    docs_by_id: dict = {}
    if ed_doc_ids:
        d_rows = _try_query(con, f"SELECT * FROM {S}.REFERENCE_DOCUMENT WHERE DOCUMENT_ID IN {_in_list(ed_doc_ids)}", label="REFERENCE_DOCUMENT")
        docs_by_id = {r["document_id"]: r for r in d_rows}

    # ---- Places per event (via PLACE_EVENT_ROLE) ----
    per_rows = _try_query(con, f"SELECT * FROM {S}.PLACE_EVENT_ROLE WHERE EVENT_ID IN {in_c}", label="PLACE_EVENT_ROLE")
    per_by_event: dict[int, list] = {}
    for r in per_rows:
        per_by_event.setdefault(r["event_id"], []).append(r)

    place_ids = list({r["place_id"] for r in per_rows if r.get("place_id")})
    places_data: dict[int, dict] = {}
    if place_ids:
        places_data = _fetch_places(con, place_ids)

    # ---- Taxonomy per classification event ----
    taxonomy_by_event: dict[int, dict] = {}
    for eid, cevt in typed_by_table.get("CLASSIFICATION_EVENT", {}).items():
        if cevt.get("class_term_id"):
            taxonomy_by_event[eid] = _fetch_taxonomy(con, cevt["class_term_id"])

    # ---- TYPE_SPECIMEN for typification events ----
    type_event_ids = list(typed_by_table.get("TYPIFICATION_EVENT", {}).keys())
    type_specimens_by_event: dict[int, list] = {}
    if type_event_ids:
        ts_rows = _try_query(
            con,
            f"SELECT * FROM {S}.TYPE_SPECIMEN WHERE EVENT_ID IN {_in_list(type_event_ids)}",
            label="TYPE_SPECIMEN",
        )
        for r in ts_rows:
            type_specimens_by_event.setdefault(r["event_id"], []).append(r)

    # ---- GENDERS_AND_STAGES + GENDER_STAGE_TERMS vocabulary ----
    gs_rows = _try_query(con, f"SELECT * FROM {S}.GENDERS_AND_STAGES WHERE EVENT_ID IN {in_c}", label="GENDERS_AND_STAGES")
    gs_by_event: dict[int, list] = {}
    for r in gs_rows:
        gs_by_event.setdefault(r["event_id"], []).append(r)

    gst_ids = list({r["gender_class_term_id"] for r in gs_rows if r.get("gender_class_term_id")})
    gst_by_id: dict = {}
    if gst_ids:
        gst_rows = _try_query(con, f"SELECT * FROM {S}.GENDER_STAGE_TERMS WHERE GENDER_CLASS_TERM_ID IN {_in_list(gst_ids)}", label="GENDER_STAGE_TERMS")
        gst_by_id = {r["gender_class_term_id"]: r for r in gst_rows}

    # ---- TYPES vocabulary (decode *type_id fields across all rows) ----
    # Collect type IDs from event subtable rows, plus EVENT.event_type and
    # NOTE/DOCUMENT type_id fields, plus coordinate-facet type columns.
    type_val_ids: set[int] = set()
    for tbl_rows in typed_by_table.values():
        for row in tbl_rows.values():
            for k, v in row.items():
                if k.endswith("type_id") and isinstance(v, int):
                    type_val_ids.add(v)
    for evt in events_rows:
        if isinstance(evt.get("event_type"), int):
            type_val_ids.add(evt["event_type"])
    for n in notes_by_id.values():
        if isinstance(n.get("type_id"), int):
            type_val_ids.add(n["type_id"])
    for d in docs_by_id.values():
        if isinstance(d.get("document_type_id"), int):
            type_val_ids.add(d["document_type_id"])
    # Also pick up MUSEUM_OBJECT.museum_object_type (passed in via context later via _TYPE_LOOKUPS)

    types_by_id: dict = {}
    if type_val_ids:
        tv_rows = _try_query(
            con,
            f"SELECT * FROM {S}.TYPES WHERE TYPE_ID IN {_in_list(sorted(type_val_ids))}",
            label="TYPES",
        )
        types_by_id = {r["type_id"]: r for r in tv_rows}

    # ---- Assemble per-event result ----
    result = []
    for emo in emo_rows:
        eid = emo["event_id"]
        evt = events_by_id.get(eid, {})
        ts_id = evt.get("timespan_id")

        entry: dict[str, Any] = {
            "EVENT_MUSEUM_OBJECT": emo,
            "EVENT": evt,
            "TIMESPAN": timespans_by_id.get(ts_id) if ts_id else None,
        }

        # Event hierarchy links
        if eid in eh_by_event:
            entry["EVENT_HIERARCHY"] = eh_by_event[eid]

        # Type-specific subtable (whichever applies)
        for tbl in event_subtables:
            typed_row = typed_by_table.get(tbl, {}).get(eid)
            if typed_row:
                entry[tbl] = typed_row

        # Measurement child rows under MEASURMENT_EVENT
        if eid in measurments_by_event:
            entry["MEASURMENT"] = measurments_by_event[eid]

        # Place revision audit trail
        if eid in pr_by_event:
            entry["PLACE_REVISION"] = pr_by_event[eid]

        # People
        pn_links = erpn_by_event.get(eid, [])
        if pn_links:
            entry["EVENT_ROLE_PERSON_NAME"] = [
                {
                    **link,
                    "_PERSON_NAME": pn_by_id.get(link.get("person_name_id")),
                    "_ROLE": roles_by_id.get(link.get("role_id")),
                }
                for link in pn_links
            ]

        actor_links = era_by_event.get(eid, [])
        if actor_links:
            entry["EVENT_ROLE_ACTOR"] = []
            for link in actor_links:
                actor = actors_by_id.get(link.get("actor_id"))
                aid = link.get("actor_id")
                entry["EVENT_ROLE_ACTOR"].append({
                    **link,
                    "_ACTOR": actor,
                    "_ACTOR_PERSON_NAME": pn_by_id.get(actor.get("valid_person_name_id")) if actor else None,
                    "_ROLE": roles_by_id.get(link.get("role_id")),
                    "_GROUPMEMBERSHIP": groupmemberships_by_actor.get(aid) if aid else None,
                })

        # Notes
        en_links = en_by_event.get(eid, [])
        if en_links:
            entry["EVENT_NOTE"] = [
                {**link, "_NOTE": notes_by_id.get(link.get("note_id"))}
                for link in en_links
            ]

        # Documents
        ed_links = ed_by_event.get(eid, [])
        if ed_links:
            entry["EVENT_DOCUMENT"] = [
                {**link, "_REFERENCE_DOCUMENT": docs_by_id.get(link.get("document_id"))}
                for link in ed_links
            ]

        # Place
        place_links = per_by_event.get(eid, [])
        if place_links:
            entry["PLACE_EVENT_ROLE"] = [
                {**link, "_PLACE_DATA": places_data.get(link.get("place_id"))}
                for link in place_links
            ]

        # Taxonomy
        if eid in taxonomy_by_event:
            entry["_TAXONOMY"] = taxonomy_by_event[eid]

        # Type specimens
        if eid in type_specimens_by_event:
            entry["TYPE_SPECIMEN"] = type_specimens_by_event[eid]

        # Genders and stages (with vocabulary decode)
        if eid in gs_by_event:
            entry["GENDERS_AND_STAGES"] = [
                {**gs, "_GENDER_STAGE_TERM": gst_by_id.get(gs.get("gender_class_term_id"))}
                for gs in gs_by_event[eid]
            ]

        # Inline TYPES decode for any *type_id fields in this entry's subtables + EVENT.event_type
        _type_decode: dict[str, Any] = {}
        for tbl in event_subtables:
            typed_row = typed_by_table.get(tbl, {}).get(eid)
            if typed_row:
                for k, v in typed_row.items():
                    if k.endswith("type_id") and isinstance(v, int) and v in types_by_id:
                        _type_decode[f"{tbl}.{k}"] = types_by_id[v]
        if isinstance(evt.get("event_type"), int) and evt["event_type"] in types_by_id:
            _type_decode["EVENT.event_type"] = types_by_id[evt["event_type"]]
        if _type_decode:
            entry["_TYPE_LOOKUPS"] = _type_decode

        result.append(entry)

    return result


def _fetch_notes_and_docs(con, object_id: int) -> dict:
    """Object-level notes and documents."""
    oid = object_id
    result: dict[str, Any] = {}

    mon_rows = _try_query(con, f"SELECT * FROM {S}.MUSEUM_OBJECT_NOTE WHERE OBJECT_ID = :oid", {"oid": oid}, "MUSEUM_OBJECT_NOTE")
    note_ids = [r["note_id"] for r in mon_rows if r.get("note_id")]
    notes_by_id: dict = {}
    if note_ids:
        n_rows = _try_query(con, f"SELECT * FROM {S}.NOTE WHERE NOTE_ID IN {_in_list(note_ids)}", label="NOTE")
        notes_by_id = {r["note_id"]: r for r in n_rows}
    result["MUSEUM_OBJECT_NOTE"] = [
        {**r, "_NOTE": notes_by_id.get(r.get("note_id"))}
        for r in mon_rows
    ]

    do_rows = _try_query(con, f"SELECT * FROM {S}.DOCUMENT_OBJECT WHERE OBJECT_ID = :oid", {"oid": oid}, "DOCUMENT_OBJECT")
    doc_ids = [r["document_id"] for r in do_rows if r.get("document_id")]
    docs_by_id: dict = {}
    if doc_ids:
        d_rows = _try_query(con, f"SELECT * FROM {S}.REFERENCE_DOCUMENT WHERE DOCUMENT_ID IN {_in_list(doc_ids)}", label="REFERENCE_DOCUMENT")
        docs_by_id = {r["document_id"]: r for r in d_rows}
    result["DOCUMENT_OBJECT"] = [
        {**r, "_REFERENCE_DOCUMENT": docs_by_id.get(r.get("document_id"))}
        for r in do_rows
    ]

    return result


def _fetch_media(con, museum_object: dict | None) -> dict:
    """Media from USD_FELLES, keyed by MEDIAGRUPPE_ENHETS_ID on MUSEUM_OBJECT."""
    if not museum_object:
        return {}
    mgeid = museum_object.get("mediagruppe_enhets_id")
    if not mgeid:
        return {"_note": "MEDIAGRUPPE_ENHETS_ID is null — no media group on this object"}

    result: dict[str, Any] = {"mediagruppe_enhets_id": mgeid}

    mge_rows = _try_query(
        con, "SELECT * FROM USD_FELLES.MEDIAGRUPPE_ENHET WHERE MEDIAGRUPPE_ENHETS_ID = :id", {"id": mgeid},
        "USD_FELLES.MEDIAGRUPPE_ENHET",
    )
    result["MEDIAGRUPPE_ENHET"] = mge_rows[0] if mge_rows else None

    mf_rows = _try_query(
        con, "SELECT * FROM USD_FELLES.MEDIA_FIL WHERE MEDIAGRUPPE_ENHETS_ID = :id", {"id": mgeid},
        "USD_FELLES.MEDIA_FIL",
    )
    result["MEDIA_FIL"] = mf_rows

    # MEDIA__PATHS is indexed by schema name; grab the relevant path entry
    mp_rows = _try_query(
        con,
        "SELECT * FROM USD_FELLES.MEDIA__PATHS WHERE SKJEMA_NAVN = 'MUSIT_BOTANIKK_FELLES'",
        label="USD_FELLES.MEDIA__PATHS",
    )
    result["MEDIA__PATHS"] = mp_rows

    # Public Unimus image URL (legacy web UI)
    if mge_rows:
        result["_unimus_url"] = f"https://www.unimus.no/felles/bilder/web_hent_bilde.php?id={mgeid}&type=jpeg"

    return result


def _fetch_digir_dwc(con, object_id: int, uuid: str | None) -> dict:
    """DiGIR/DwC pre-flattened export row (DC_VASCULAR_FELLES), if accessible."""
    result: dict[str, Any] = {}

    # Try via UUID first (most likely accessible path)
    if uuid:
        # Attempt to find the record in collection-specific Darwin Core views (e.g. V_DC_O_VASCULAR)
        # Get institution/collection codes from MUSEUM_OBJECT if possible, or try common patterns.
        prefixes = ["V_DC_O_VASCULAR", "V_DC_TRH_VASCULAR", "V_DC_BG_VASCULAR", "V_DC_TROM_VASCULAR", "DC_VASCULAR_FELLES"]
        for view_name in prefixes:
            rows = _try_query(
                con,
                f"SELECT * FROM DIGIR_MUSIT.{view_name} WHERE LOWER(TRIM(UUID)) = LOWER(TRIM(:uuid))",
                {"uuid": uuid},
                f"DIGIR_MUSIT.{view_name} (by UUID)",
            )
            if rows:
                result[view_name] = rows[0]
                break
            
            rows = _try_query(
                con,
                f"SELECT * FROM DIGIR_MUSIT.{view_name} WHERE OBJECT_ID = :oid",
                {"oid": object_id},
                f"DIGIR_MUSIT.{view_name} (by OBJECT_ID)",
            )
            if rows:
                result[view_name] = rows[0]
                break

    return result


def _resolve_catalog_number(con, catalog: str) -> list[int]:
    """Resolve a catalog number string or integer to MUSIT OBJECT_IDs."""
    print(f"  Resolving: {catalog!r}", file=sys.stderr)

    # Exact match on IDENTIFIER_STRING
    rows = _try_query(
        con, f"SELECT OBJECT_ID FROM {S}.MUSEUM_OBJECT WHERE IDENTIFIER_STRING = :cat", {"cat": catalog},
        "resolve IDENTIFIER_STRING (exact)",
    )
    if rows:
        return [r["object_id"] for r in rows]

    # Case-insensitive match
    rows = _try_query(
        con,
        f"SELECT OBJECT_ID FROM {S}.MUSEUM_OBJECT WHERE UPPER(IDENTIFIER_STRING) = UPPER(:cat)",
        {"cat": catalog},
        "resolve IDENTIFIER_STRING (upper)",
    )
    if rows:
        return [r["object_id"] for r in rows]

    # LIKE (catch "O-V-123456" when stored with extra whitespace etc.)
    rows = _try_query(
        con,
        f"SELECT OBJECT_ID FROM {S}.MUSEUM_OBJECT WHERE IDENTIFIER_STRING LIKE :pat",
        {"pat": f"%{catalog}%"},
        "resolve IDENTIFIER_STRING (LIKE)",
    )
    if rows:
        return [r["object_id"] for r in rows]

    # Numeric: try IDENTIFIER_NUM
    try:
        num = int(catalog.replace(" ", ""))
        rows = _try_query(
            con, f"SELECT OBJECT_ID FROM {S}.MUSEUM_OBJECT WHERE IDENTIFIER_NUM = :num", {"num": num},
            "resolve IDENTIFIER_NUM",
        )
        if rows:
            return [r["object_id"] for r in rows]
    except ValueError:
        pass

    # Try DiGIR DwC view CATALOGNUMBER (read-only fallback; join back to OBJECT_ID via UUID)
    rows = _try_query(
        con,
        "SELECT oa.object_id FROM DIGIR_MUSIT.DC_VASCULAR_FELLES v"
        f" JOIN {S}.OBJECT_ATTRIBUTES oa ON LOWER(TRIM(oa.uuid)) = LOWER(TRIM(v.uuid))"
        " WHERE v.catalognumber = :cat AND v.uuid IS NOT NULL",
        {"cat": catalog},
        "resolve via DIGIR_MUSIT.DC_VASCULAR_FELLES.CATALOGNUMBER",
    )
    if rows:
        return [r["object_id"] for r in rows]

    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump ALL Oracle source data for a catalog number as JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--catalog", "-c", metavar="CAT",
                     help="Catalog number (e.g. 'O-V-123456', 'TRH-V-241112', '241112')")
    grp.add_argument("--object-id", "-i", type=int, metavar="OID",
                     help="MUSIT MUSEUM_OBJECT.OBJECT_ID (skip catalog resolution)")
    parser.add_argument("--env", default="prod", choices=["prod", "test"],
                        help="Oracle environment (default: prod)")
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="Write JSON to FILE instead of stdout")
    parser.add_argument("--compact", action="store_true",
                        help="Compact JSON (no indentation); default is pretty-printed")
    parser.add_argument(
        "--include-lobs",
        action="store_true",
        help="Read full LOB/CLOB/BLOB contents (default: size-only placeholder; avoids bulk transfer).",
    )
    args = parser.parse_args()

    global _INCLUDE_ORACLE_LOBS
    _INCLUDE_ORACLE_LOBS = bool(
        args.include_lobs
        or os.getenv("ORACLE_CATALOG_INCLUDE_LOBS", "").lower() in ("1", "true", "yes")
    )

    _init_oracle_client()
    print(
        f"[oracle_catalog_dump] env={args.env.upper()}  "
        f"{os.environ.get(f'ORACLE_{args.env.upper()}_HOST', 'localhost')}:"
        f"{os.environ.get(f'ORACLE_{args.env.upper()}_PORT', str(_LOCAL_PORTS[args.env]))}  "
        f"mode={_current_mode}",
        file=sys.stderr,
    )

    try:
        con = _connect(args.env)
    except oracledb.DatabaseError as exc:
        sys.exit(f"Connection failed: {exc}")

    print("[oracle_catalog_dump] connected", file=sys.stderr)

    catalog_input: str
    object_ids: list[int]

    if args.object_id is not None:
        object_ids = [args.object_id]
        catalog_input = str(args.object_id)
    else:
        catalog_input = args.catalog
        object_ids = _resolve_catalog_number(con, args.catalog)
        if not object_ids:
            con.close()
            sys.exit(f"No MUSEUM_OBJECT found for: {args.catalog!r}")
        if len(object_ids) > 1:
            print(f"  Warning: {len(object_ids)} objects matched — dumping all", file=sys.stderr)

    # Collect all MUSEUM_OBJECT.museum_object_type IDs upfront so we can decode
    # them alongside the per-event TYPES vocabulary.
    mo_type_ids: set[int] = set()

    results = []
    for oid in object_ids:
        print(f"  Fetching OBJECT_ID={oid} ...", file=sys.stderr)

        obj_core = _fetch_object_core(con, oid)
        museum_object = obj_core.get("MUSEUM_OBJECT")
        object_attributes = obj_core.get("OBJECT_ATTRIBUTES")

        uuid = (object_attributes or {}).get("uuid") if object_attributes else None

        # Track museum_object_type for TYPES decode
        if museum_object and isinstance(museum_object.get("museum_object_type"), int):
            mo_type_ids.add(museum_object["museum_object_type"])

        doc: dict[str, Any] = {
            "_meta": {
                "catalog_number_input": catalog_input,
                "object_id": oid,
                "schema": S,
                "extracted_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "env": args.env,
            },
            **obj_core,
            "events": _fetch_events(con, oid),
            **_fetch_notes_and_docs(con, oid),
            "media": _fetch_media(con, museum_object),
            "digir_dwc": _fetch_digir_dwc(con, oid, uuid),
        }

        # Decode MUSEUM_OBJECT.museum_object_type via TYPES vocabulary
        if mo_type_ids:
            mo_type_rows = _try_query(
                con,
                f"SELECT * FROM {S}.TYPES WHERE TYPE_ID IN {_in_list(sorted(mo_type_ids))}",
                label="TYPES (museum_object_type)",
            )
            mo_types_by_id = {r["type_id"]: r for r in mo_type_rows}
            if museum_object and museum_object.get("museum_object_type") in mo_types_by_id:
                doc["_MUSEUM_OBJECT_TYPE"] = mo_types_by_id[museum_object["museum_object_type"]]

        results.append(doc)
        print(f"  Done OBJECT_ID={oid}", file=sys.stderr)

    con.close()

    output_obj = results[0] if len(results) == 1 else results
    indent = None if args.compact else 2
    json_str = json.dumps(output_obj, indent=indent, ensure_ascii=False, cls=_Encoder)

    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Written to {args.output}  ({len(json_str):,} bytes)", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
