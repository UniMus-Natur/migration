#!/usr/bin/env python3
"""
Compare Oracle source data with Specify7 sink data for one or more catalog numbers.

Invokes oracle_catalog_dump.py and specify_catalog_dump.py as subprocesses, runs
a battery of checks, and emits results in terminal, JSON, and/or Markdown formats.

Usage (standalone):
    python scripts/migration_compare.py --catalog "O-V-14399" --collection-code NHM-karplanter
    python scripts/migration_compare.py --fixture scripts/test_fixtures.yaml
    python scripts/migration_compare.py --fixture scripts/test_fixtures.yaml --format all
    python scripts/migration_compare.py --fixture scripts/test_fixtures.yaml --output-dir /tmp/reports

After sourcing port-forward.sh the shell alias also works:
    migration_compare --catalog "O-V-14399" --collection-code NHM-karplanter

Credentials for Oracle and MariaDB are read from environment variables or the .env
file in the project root (same as the individual dump scripts).
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# .env loading
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
_load_dotenv(_SCRIPTS_DIR.parent / ".env")


# ---------------------------------------------------------------------------
# Check result type
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    status: str          # PASS | FAIL | WARN | SKIP
    oracle_val: Any = None
    specify_val: Any = None
    detail: str = ""

    @property
    def icon(self) -> str:
        return {"PASS": "✓", "FAIL": "✗", "WARN": "~", "SKIP": "-"}.get(self.status, "?")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _str(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _similarity(a: str, b: str) -> float:
    """Sequence-based similarity ratio in [0.0, 1.0]."""
    a_norm = " ".join(a.lower().split())
    b_norm = " ".join(b.lower().split())
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


def _date_str(v: Any) -> str:
    """Normalise various date representations to YYYY-MM-DD or return as-is."""
    s = _str(v)
    # Handle ISO datetime
    if "T" in s:
        s = s.split("T")[0]
    return s


# ---------------------------------------------------------------------------
# Oracle adapter
# ---------------------------------------------------------------------------

class OracleAdapter:
    """Extract a normalised, flat view of a single Oracle dump document."""

    def __init__(self, doc: dict) -> None:
        self._doc = doc
        self._mo: dict = doc.get("MUSEUM_OBJECT") or {}
        self._oa: dict = doc.get("OBJECT_ATTRIBUTES") or {}
        self._events: list[dict] = doc.get("events") or []

    # -- Identity --

    @property
    def catalog_number(self) -> str:
        return _str(self._mo.get("identifier_string"))

    @property
    def object_id(self) -> int | None:
        return self._mo.get("object_id")

    @property
    def identifier_num(self) -> str:
        num = self._mo.get("identifier_num")
        return _str(num) if num is not None else ""

    @property
    def uuid(self) -> str:
        return _str(self._oa.get("uuid"))

    # -- Collecting event --

    def _collecting_events(self) -> list[dict]:
        return [e for e in self._events if e.get("COLLECTING_EVENT")]

    @property
    def collecting_start_date(self) -> str:
        for evt in self._collecting_events():
            ts = evt.get("TIMESPAN") or {}
            d = ts.get("from_date") or ts.get("interpreted")
            if d:
                return _date_str(d)
        return ""

    @property
    def verbatim_date(self) -> str:
        for evt in self._collecting_events():
            ts = evt.get("TIMESPAN") or {}
            v = ts.get("time_as_text")
            if v:
                return _str(v)
        return ""

    @property
    def locality_text(self) -> str:
        for evt in self._collecting_events():
            per_links = evt.get("PLACE_EVENT_ROLE") or []
            for link in per_links:
                pd = link.get("_PLACE_DATA") or {}
                place = pd.get("PLACE") or {}
                name = place.get("place_name_agg")
                if name:
                    return _str(name)
                for lp_entry in pd.get("LOCALITY_PLACE") or []:
                    lp = lp_entry.get("_LOCALITY_PLACE") or {}
                    loc = lp.get("locality")
                    if loc:
                        return _str(loc)
        return ""

    @property
    def collectors(self) -> list[str]:
        names: list[str] = []
        for evt in self._collecting_events():
            for link in evt.get("EVENT_ROLE_PERSON_NAME") or []:
                pn = link.get("_PERSON_NAME") or {}
                parts = [
                    pn.get("person_surname") or "",
                    pn.get("person_given_name") or "",
                    pn.get("person_middle_name") or "",
                ]
                name = " ".join(p for p in parts if p).strip()
                if name:
                    names.append(name)
            for link in evt.get("EVENT_ROLE_ACTOR") or []:
                actor = link.get("_ACTOR") or {}
                pn = link.get("_ACTOR_PERSON_NAME") or {}
                if pn:
                    parts = [
                        pn.get("person_surname") or "",
                        pn.get("person_given_name") or "",
                        pn.get("person_middle_name") or "",
                    ]
                    name = " ".join(p for p in parts if p).strip()
                elif actor.get("actorname"):
                    name = _str(actor["actorname"])
                else:
                    name = ""
                if name:
                    names.append(name)
        return names

    # -- Determinations (classification events, sorted newest first) --

    def _classification_events(self) -> list[dict]:
        evts = [e for e in self._events if e.get("CLASSIFICATION_EVENT")]
        def _sort_key(e: dict) -> str:
            ts = e.get("TIMESPAN") or {}
            return _date_str(ts.get("from_date") or ts.get("interpreted") or "")
        return sorted(evts, key=_sort_key, reverse=True)

    @property
    def determinations(self) -> list[dict]:
        result = []
        for evt in self._classification_events():
            ce = evt.get("CLASSIFICATION_EVENT") or {}
            tax = evt.get("_TAXONOMY") or {}
            ct = tax.get("CLASSIFICATION_TERM") or {}

            # Build taxon name from classterm or latin names
            taxon_name = _str(ct.get("classterm") or ce.get("detname_orig") or "")
            if not taxon_name:
                lns = tax.get("LATIN_NAMES") or []
                if lns:
                    taxon_name = _str(lns[0].get("full_name") or lns[0].get("latin_name") or "")

            ts = evt.get("TIMESPAN") or {}
            det_date = _date_str(ts.get("from_date") or ts.get("interpreted") or "")

            result.append({"taxon": taxon_name, "date": det_date})
        return result

    # -- Media --

    @property
    def media_ids(self) -> list[str]:
        media = self._doc.get("media") or {}
        return [_str(f.get("mediafil_id")) for f in (media.get("MEDIA_FIL") or []) if f.get("mediafil_id")]

    # -- Notes --

    @property
    def notes(self) -> list[str]:
        result = []
        for entry in self._doc.get("MUSEUM_OBJECT_NOTE") or []:
            note = entry.get("_NOTE") or {}
            text = note.get("note_text") or note.get("long_note")
            if text:
                result.append(_str(text))
        return result


# ---------------------------------------------------------------------------
# Specify adapter
# ---------------------------------------------------------------------------

class SpecifyAdapter:
    """Extract a normalised, flat view of a single Specify7 dump document.

    specify_catalog_dump.py emits:
        {"_meta": {...}, "tables": {"collectionobject": [...], "collectingevent": [...], ...}}

    All navigation is done by joining on FK values within the flat tables dict.
    """

    def __init__(self, doc: dict) -> None:
        self._doc = doc
        self._tables: dict[str, list[dict]] = doc.get("tables") or {}

    # -- Low-level helpers --

    def _tbl(self, table: str) -> list[dict]:
        return self._tables.get(table) or []

    def _find(self, table: str, col: str, val: Any) -> dict | None:
        for row in self._tbl(table):
            if row.get(col) == val:
                return row
        return None

    def _find_all(self, table: str, col: str, val: Any) -> list[dict]:
        return [row for row in self._tbl(table) if row.get(col) == val]

    # -- Root collectionobject row --

    @property
    def _co(self) -> dict:
        rows = self._tbl("collectionobject")
        return rows[0] if rows else {}

    # -- Identity --

    @property
    def catalog_number(self) -> str:
        return _str(self._co.get("catalognumber"))

    @property
    def guid(self) -> str:
        return _str(self._co.get("guid"))

    @property
    def field_number(self) -> str:
        return _str(self._co.get("fieldnumber"))

    @property
    def remarks(self) -> str:
        return _str(self._co.get("remarks"))

    @property
    def text3_json(self) -> dict:
        return self._co.get("_text3_json") or {}

    # -- Collecting event --

    def _ce(self) -> dict:
        ce_id = self._co.get("collectingeventid")
        if ce_id is None:
            return {}
        return self._find("collectingevent", "collectingeventid", ce_id) or {}

    @property
    def collecting_start_date(self) -> str:
        ce = self._ce()
        d = ce.get("startdate")
        return _date_str(d) if d else ""

    @property
    def verbatim_date(self) -> str:
        ce = self._ce()
        return _str(ce.get("verbatimdate") or ce.get("startdateverbatim") or "")

    @property
    def locality_text(self) -> str:
        ce = self._ce()
        loc_id = ce.get("localityid")
        if loc_id is None:
            return ""
        loc = self._find("locality", "localityid", loc_id) or {}
        return _str(loc.get("localityname") or loc.get("namedplace") or "")

    @property
    def collectors(self) -> list[str]:
        ce = self._ce()
        ce_id = ce.get("collectingeventid")
        if ce_id is None:
            return []
        col_rows = self._find_all("collector", "collectingeventid", ce_id)
        col_rows.sort(key=lambda r: r.get("ordernumber") or 0)
        names: list[str] = []
        for col_row in col_rows:
            agent_id = col_row.get("agentid")
            if agent_id is None:
                continue
            agent = self._find("agent", "agentid", agent_id) or {}
            parts = [
                agent.get("lastname") or "",
                agent.get("firstname") or "",
                agent.get("middleinitial") or "",
            ]
            name = " ".join(p for p in parts if p).strip()
            if not name:
                name = _str(agent.get("name") or "")
            if name:
                names.append(name)
        return names

    # -- Determinations (current first, then newest first) --

    @property
    def determinations(self) -> list[dict]:
        co_id = self._co.get("collectionobjectid")
        if co_id is None:
            return []
        det_rows = self._find_all("determination", "collectionobjectid", co_id)
        result = []
        for det in det_rows:
            taxon_name = _str(det.get("text1") or det.get("nameusage") or "")
            det_date = _date_str(det.get("determineddate") or "")
            result.append({"taxon": taxon_name, "date": det_date, "iscurrent": det.get("iscurrent")})
        current = [d for d in result if d.get("iscurrent")]
        non_current = sorted(
            [d for d in result if not d.get("iscurrent")],
            key=lambda d: d.get("date", ""),
            reverse=True,
        )
        return current + non_current

    # -- Media / attachments --

    @property
    def attachment_urls(self) -> list[str]:
        co_id = self._co.get("collectionobjectid")
        if co_id is None:
            return []
        urls: list[str] = []
        for coa in self._find_all("collectionobjectattachment", "collectionobjectid", co_id):
            att_id = coa.get("attachmentid")
            if att_id is None:
                continue
            att = self._find("attachment", "attachmentid", att_id) or {}
            url = att.get("attachmentlocation") or att.get("origfilename") or ""
            if url:
                urls.append(_str(url))
        return urls

    @property
    def attachment_count(self) -> int:
        co_id = self._co.get("collectionobjectid")
        if co_id is None:
            return 0
        return len(self._find_all("collectionobjectattachment", "collectionobjectid", co_id))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def run_checks(oracle: OracleAdapter, specify: SpecifyAdapter) -> list[CheckResult]:
    checks: list[CheckResult] = []

    def chk(name: str, status: str, ov: Any = None, sv: Any = None, detail: str = "") -> None:
        checks.append(CheckResult(name=name, status=status, oracle_val=ov, specify_val=sv, detail=detail))

    # 1. catalog_number_present
    if specify.catalog_number:
        chk("catalog_number_present", "PASS", sv=specify.catalog_number)
    else:
        chk("catalog_number_present", "FAIL", detail="No collectionobject found in Specify")
        return checks  # no point running further checks

    # 2. catalog_number_match
    oc = oracle.catalog_number
    sc = specify.catalog_number
    if oc == sc:
        chk("catalog_number_match", "PASS", ov=oc, sv=sc)
    elif oc.lower() == sc.lower():
        chk("catalog_number_match", "WARN", ov=oc, sv=sc, detail="case difference")
    else:
        chk("catalog_number_match", "FAIL", ov=oc, sv=sc)

    # 3. uuid_in_remarks
    o_uuid = oracle.uuid
    remarks = specify.remarks
    guid = specify.guid
    if not o_uuid:
        chk("uuid_in_remarks", "SKIP", detail="Oracle UUID is null")
    elif o_uuid.lower() in remarks.lower() or o_uuid.lower() in guid.lower():
        chk("uuid_in_remarks", "PASS", ov=o_uuid, sv=f"remarks/guid contains UUID")
    else:
        chk("uuid_in_remarks", "FAIL", ov=o_uuid, sv=f"remarks={remarks!r}  guid={guid!r}")

    # 4. identifier_num_match
    o_num = oracle.identifier_num
    s_fn = specify.field_number
    if not o_num:
        chk("identifier_num_match", "SKIP", detail="Oracle IDENTIFIER_NUM is null")
    elif o_num == s_fn:
        chk("identifier_num_match", "PASS", ov=o_num, sv=s_fn)
    else:
        chk("identifier_num_match", "FAIL", ov=o_num, sv=s_fn)

    # 5. collecting_start_date
    o_date = oracle.collecting_start_date
    s_date = specify.collecting_start_date
    if not o_date and not s_date:
        chk("collecting_start_date", "SKIP", detail="Both null")
    elif not o_date:
        chk("collecting_start_date", "WARN", ov=o_date, sv=s_date, detail="Oracle date missing")
    elif not s_date:
        chk("collecting_start_date", "FAIL", ov=o_date, sv=s_date, detail="Specify date missing")
    elif o_date == s_date:
        chk("collecting_start_date", "PASS", ov=o_date, sv=s_date)
    else:
        # Allow 1-day tolerance for timezone edge cases
        try:
            od = datetime.date.fromisoformat(o_date)
            sd = datetime.date.fromisoformat(s_date)
            delta = abs((od - sd).days)
            if delta <= 1:
                chk("collecting_start_date", "WARN", ov=o_date, sv=s_date, detail=f"off by {delta} day(s)")
            else:
                chk("collecting_start_date", "FAIL", ov=o_date, sv=s_date, detail=f"off by {delta} days")
        except ValueError:
            chk("collecting_start_date", "FAIL", ov=o_date, sv=s_date)

    # 6. verbatim_date
    o_vd = oracle.verbatim_date
    s_vd = specify.verbatim_date
    if not o_vd and not s_vd:
        chk("verbatim_date", "SKIP", detail="Both null")
    elif not o_vd:
        chk("verbatim_date", "WARN", ov=o_vd, sv=s_vd, detail="Oracle verbatim date missing")
    elif not s_vd:
        chk("verbatim_date", "WARN", ov=o_vd, sv=s_vd, detail="Specify verbatim date missing")
    elif o_vd == s_vd:
        chk("verbatim_date", "PASS", ov=o_vd, sv=s_vd)
    else:
        score = _similarity(o_vd, s_vd)
        status = "WARN" if score >= 0.8 else "FAIL"
        chk("verbatim_date", status, ov=o_vd, sv=s_vd, detail=f"similarity={score:.2f}")

    # 7. locality_text (fuzzy)
    o_loc = oracle.locality_text
    s_loc = specify.locality_text
    if not o_loc and not s_loc:
        chk("locality_text", "SKIP", detail="Both null")
    elif not o_loc:
        chk("locality_text", "WARN", ov=o_loc, sv=s_loc, detail="Oracle locality missing")
    elif not s_loc:
        chk("locality_text", "FAIL", ov=o_loc, sv=s_loc, detail="Specify locality missing")
    else:
        score = _similarity(o_loc, s_loc)
        if score >= 0.85:
            chk("locality_text", "PASS", ov=o_loc, sv=s_loc, detail=f"similarity={score:.2f}")
        elif score >= 0.6:
            chk("locality_text", "WARN", ov=o_loc, sv=s_loc, detail=f"similarity={score:.2f}")
        else:
            chk("locality_text", "FAIL", ov=o_loc, sv=s_loc, detail=f"similarity={score:.2f}")

    # 8. collectors_count
    o_cols = oracle.collectors
    s_cols = specify.collectors
    if not o_cols and not s_cols:
        chk("collectors_count", "SKIP", detail="Both empty")
    elif len(o_cols) == len(s_cols):
        chk("collectors_count", "PASS", ov=len(o_cols), sv=len(s_cols))
    else:
        status = "WARN" if abs(len(o_cols) - len(s_cols)) <= 1 else "FAIL"
        chk("collectors_count", status, ov=len(o_cols), sv=len(s_cols))

    # 9. collectors_names (pairwise similarity)
    if o_cols and s_cols:
        pairs = list(zip(o_cols, s_cols))
        scores = [_similarity(a, b) for a, b in pairs]
        avg = sum(scores) / len(scores) if scores else 1.0
        detail_parts = [f"'{a}' vs '{b}' ({sc:.2f})" for (a, b), sc in zip(pairs, scores)]
        status = "PASS" if avg >= 0.85 else ("WARN" if avg >= 0.6 else "FAIL")
        chk("collectors_names", status, ov=o_cols, sv=s_cols, detail="; ".join(detail_parts))
    else:
        chk("collectors_names", "SKIP", detail="One or both collector lists empty")

    # 10. determination_count
    o_dets = oracle.determinations
    s_dets = specify.determinations
    if not o_dets and not s_dets:
        chk("determination_count", "SKIP", detail="Both empty")
    elif len(o_dets) == len(s_dets):
        chk("determination_count", "PASS", ov=len(o_dets), sv=len(s_dets))
    else:
        status = "WARN" if abs(len(o_dets) - len(s_dets)) <= 1 else "FAIL"
        chk("determination_count", status, ov=len(o_dets), sv=len(s_dets))

    # 11. determination_taxa_latest (most recent)
    if o_dets and s_dets:
        o_tx = o_dets[0]["taxon"]
        s_tx = s_dets[0]["taxon"]
        score = _similarity(o_tx, s_tx)
        status = "PASS" if score >= 0.85 else ("WARN" if score >= 0.6 else "FAIL")
        chk("determination_taxa_latest", status, ov=o_tx, sv=s_tx, detail=f"similarity={score:.2f}")
    else:
        chk("determination_taxa_latest", "SKIP", detail="No determinations to compare")

    # 12. determination_dates (list match)
    if o_dets and s_dets:
        o_dates = [d["date"] for d in o_dets]
        s_dates = [d["date"] for d in s_dets]
        if o_dates == s_dates:
            chk("determination_dates", "PASS", ov=o_dates, sv=s_dates)
        else:
            o_set = set(d for d in o_dates if d)
            s_set = set(d for d in s_dates if d)
            missing = o_set - s_set
            extra = s_set - o_set
            parts = []
            if missing:
                parts.append(f"missing in Specify: {sorted(missing)}")
            if extra:
                parts.append(f"extra in Specify: {sorted(extra)}")
            status = "WARN" if (not missing and extra) else "FAIL"
            chk("determination_dates", status, ov=o_dates, sv=s_dates, detail="; ".join(parts))
    else:
        chk("determination_dates", "SKIP", detail="No determinations to compare")

    # 13. media_count
    o_media = oracle.media_ids
    s_att_count = specify.attachment_count
    if not o_media and s_att_count == 0:
        chk("media_count", "SKIP", detail="Both empty")
    elif len(o_media) == s_att_count:
        chk("media_count", "PASS", ov=len(o_media), sv=s_att_count)
    else:
        status = "WARN" if abs(len(o_media) - s_att_count) <= 1 else "FAIL"
        chk("media_count", status, ov=len(o_media), sv=s_att_count)

    # 14. media_ids_in_specify (each Oracle mediafil_id should appear in an attachment URL)
    s_urls = specify.attachment_urls
    if not o_media:
        chk("media_ids_in_specify", "SKIP", detail="No Oracle media")
    elif not s_urls:
        chk("media_ids_in_specify", "FAIL", ov=o_media, sv=[], detail="No Specify attachments at all")
    else:
        missing_ids = [mid for mid in o_media if not any(mid in url for url in s_urls)]
        if not missing_ids:
            chk("media_ids_in_specify", "PASS", ov=o_media, sv=s_urls)
        else:
            chk("media_ids_in_specify", "FAIL", ov=o_media, sv=s_urls,
                detail=f"mediafil_ids not found in any URL: {missing_ids}")

    return checks


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run_dump(script: str, extra_args: list[str], env: dict | None = None) -> dict:
    """Run a dump script as a subprocess and parse its stdout as JSON."""
    cmd = [sys.executable, str(_SCRIPTS_DIR / script)] + extra_args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{script} exited {result.returncode}:\n{result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{script} produced invalid JSON: {exc}\n{result.stdout[:500]}") from exc


def _oracle_dump(catalog: str, oracle_env: str) -> dict:
    return _run_dump(
        "oracle_catalog_dump.py",
        ["--catalog", catalog, "--env", oracle_env, "--compact"],
    )


def _specify_dump(catalog: str, collection_code: str) -> dict:
    return _run_dump(
        "specify_catalog_dump.py",
        ["--catalog", catalog, "--collection-code", collection_code, "--compact"],
    )


# ---------------------------------------------------------------------------
# Output renderers
# ---------------------------------------------------------------------------

_STATUS_COLORS = {
    "PASS": "\033[32m",   # green
    "FAIL": "\033[31m",   # red
    "WARN": "\033[33m",   # yellow
    "SKIP": "\033[90m",   # dark grey
}
_RESET = "\033[0m"


def _colorize(status: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return _STATUS_COLORS.get(status, "") + text + _RESET


def render_terminal(catalog: str, checks: list[CheckResult]) -> None:
    width_name = max(len(c.name) for c in checks) if checks else 24
    header = f"{'Check':<{width_name}}  {'Status':<6}  Detail"
    print(f"\n{'─' * len(header)}")
    print(f"  Specimen: {catalog}")
    print(f"{'─' * len(header)}")
    print(f"  {header}")
    print(f"  {'─' * (len(header) - 2)}")
    for c in checks:
        status_str = _colorize(c.status, f"{c.icon} {c.status:<4}")
        detail = c.detail or ""
        if not detail and c.oracle_val is not None and c.specify_val is not None and c.status != "PASS":
            detail = f"oracle={c.oracle_val!r}  specify={c.specify_val!r}"
        print(f"  {c.name:<{width_name}}  {status_str}  {detail}")
    counts = _summary_counts(checks)
    summary = (
        f"  pass={counts['PASS']}  fail={counts['FAIL']}  "
        f"warn={counts['WARN']}  skip={counts['SKIP']}"
    )
    print(f"  {'─' * (len(header) - 2)}")
    print(_colorize("FAIL" if counts["FAIL"] else ("WARN" if counts["WARN"] else "PASS"), summary))
    print(f"{'─' * len(header)}\n")


def _summary_counts(checks: list[CheckResult]) -> dict[str, int]:
    counts: dict[str, int] = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    return counts


def render_json(catalog: str, checks: list[CheckResult], oracle_doc: dict, specify_doc: dict) -> dict:
    counts = _summary_counts(checks)
    return {
        "catalog": catalog,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "summary": counts,
        "overall": "PASS" if counts["FAIL"] == 0 and counts["WARN"] == 0 else (
            "WARN" if counts["FAIL"] == 0 else "FAIL"
        ),
        "checks": [
            {
                "name": c.name,
                "status": c.status,
                "oracle_val": c.oracle_val,
                "specify_val": c.specify_val,
                "detail": c.detail,
            }
            for c in checks
        ],
    }


def render_markdown(
    catalog: str,
    checks: list[CheckResult],
    oracle: OracleAdapter,
    specify: SpecifyAdapter,
    oracle_doc: dict,
    specify_doc: dict,
) -> str:
    lines: list[str] = []

    lines.append(f"# Migration Evidence: {catalog}\n")
    lines.append(f"*Generated: {datetime.datetime.now(datetime.UTC).isoformat()}*\n")

    # -- Identity section --
    lines.append("## Identity\n")
    lines.append("| Field | Oracle source | Specify sink | Match |")
    lines.append("|---|---|---|---|")
    lines.append(f"| catalog_number | {oracle.catalog_number} | {specify.catalog_number} | {_match_icon(oracle.catalog_number, specify.catalog_number)} |")
    lines.append(f"| uuid / guid | {oracle.uuid or '—'} | {specify.guid or '—'} | {_uuid_match_icon(oracle.uuid, specify.guid, specify.remarks)} |")
    lines.append(f"| identifier_num / fieldnumber | {oracle.identifier_num or '—'} | {specify.field_number or '—'} | {_match_icon(oracle.identifier_num, specify.field_number)} |")
    lines.append("")

    # -- Collecting event section --
    lines.append("## Collecting Event\n")
    lines.append("| Field | Oracle | Specify | Match |")
    lines.append("|---|---|---|---|")
    lines.append(f"| start date | {oracle.collecting_start_date or '—'} | {specify.collecting_start_date or '—'} | {_match_icon(oracle.collecting_start_date, specify.collecting_start_date)} |")
    lines.append(f"| verbatim date | {oracle.verbatim_date or '—'} | {specify.verbatim_date or '—'} | {_match_icon(oracle.verbatim_date, specify.verbatim_date)} |")
    lines.append(f"| locality text | {_trunc(oracle.locality_text)} | {_trunc(specify.locality_text)} | {_fuzzy_icon(oracle.locality_text, specify.locality_text)} |")
    o_cols = oracle.collectors
    s_cols = specify.collectors
    for i, (oc_name, sc_name) in enumerate(zip(o_cols, s_cols)):
        lines.append(f"| collector {i + 1} | {oc_name} | {sc_name} | {_fuzzy_icon(oc_name, sc_name)} |")
    if len(o_cols) > len(s_cols):
        for i in range(len(s_cols), len(o_cols)):
            lines.append(f"| collector {i + 1} | {o_cols[i]} | — | ✗ |")
    elif len(s_cols) > len(o_cols):
        for i in range(len(o_cols), len(s_cols)):
            lines.append(f"| collector {i + 1} | — | {s_cols[i]} | ✗ |")
    lines.append("")

    # -- Determinations section --
    lines.append("## Determinations\n")
    lines.append("| # | Oracle taxon (date) | Specify text1 (date) | Match |")
    lines.append("|---|---|---|---|")
    o_dets = oracle.determinations
    s_dets = specify.determinations
    max_dets = max(len(o_dets), len(s_dets), 1)
    for i in range(max_dets):
        o_det = o_dets[i] if i < len(o_dets) else None
        s_det = s_dets[i] if i < len(s_dets) else None
        o_cell = f"{o_det['taxon']} ({o_det['date']})" if o_det else "—"
        s_cell = f"{s_det['taxon']} ({s_det['date']})" if s_det else "—"
        match = _fuzzy_icon(o_det["taxon"] if o_det else "", s_det["taxon"] if s_det else "")
        label = f"{i + 1}" + (" (latest)" if i == 0 else "")
        lines.append(f"| {label} | {_trunc(o_cell)} | {_trunc(s_cell)} | {match} |")
    lines.append("")

    # -- Media / attachments section --
    lines.append("## Attachments\n")
    o_media = oracle.media_ids
    s_urls = specify.attachment_urls
    if o_media or s_urls:
        lines.append("| Oracle mediafil_id | Specify attachment URL | Match |")
        lines.append("|---|---|---|")
        for mid in o_media:
            matched_url = next((u for u in s_urls if mid in u), "—")
            icon = "✓" if matched_url != "—" else "✗"
            lines.append(f"| {mid} | {_trunc(matched_url, 80)} | {icon} |")
        for url in s_urls:
            if not any(mid in url for mid in o_media):
                lines.append(f"| — | {_trunc(url, 80)} | ✗ |")
    else:
        lines.append("*No media on either side.*")
    lines.append("")

    # -- Check results table --
    lines.append("## Check Results\n")
    lines.append("| Check | Status | Detail |")
    lines.append("|---|---|---|")
    for c in checks:
        detail = c.detail or ""
        if not detail and c.oracle_val is not None and c.specify_val is not None and c.status != "PASS":
            detail = f"oracle=`{c.oracle_val}`  specify=`{c.specify_val}`"
        lines.append(f"| {c.name} | {c.icon} {c.status} | {detail} |")
    lines.append("")

    counts = _summary_counts(checks)
    lines.append(
        f"**Summary: {counts['PASS']} pass  {counts['FAIL']} fail  "
        f"{counts['WARN']} warn  {counts['SKIP']} skip**\n"
    )

    # -- Unmapped source fields (co.text3) --
    text3_json = specify.text3_json or None
    if text3_json:
        lines.append("## Unmapped source fields (from co.text3)\n")
        lines.append("```json")
        lines.append(json.dumps(text3_json, indent=2, ensure_ascii=False))
        lines.append("```\n")

    lines.append("---")
    lines.append("*Generated by migration_compare.py — paste this card into an LLM to get a migration quality judgement.*")

    return "\n".join(lines)


def _match_icon(a: Any, b: Any) -> str:
    if not a and not b:
        return "-"
    return "✓" if _str(a) == _str(b) else "✗"


def _fuzzy_icon(a: str, b: str) -> str:
    if not a and not b:
        return "-"
    if not a or not b:
        return "✗"
    score = _similarity(a, b)
    if score >= 0.85:
        return "✓"
    if score >= 0.6:
        return "~"
    return "✗"


def _uuid_match_icon(uuid: str, guid: str, remarks: str) -> str:
    if not uuid:
        return "-"
    if uuid.lower() in guid.lower() or uuid.lower() in remarks.lower():
        return "✓"
    return "✗"


def _trunc(s: str, n: int = 60) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------------------
# Per-specimen run
# ---------------------------------------------------------------------------

@dataclass
class SpecimenReport:
    catalog: str
    collection_code: str
    oracle_env: str
    checks: list[CheckResult] = field(default_factory=list)
    oracle_doc: dict = field(default_factory=dict)
    specify_doc: dict = field(default_factory=dict)
    error: str = ""


def _run_specimen(catalog: str, collection_code: str, oracle_env: str) -> SpecimenReport:
    report = SpecimenReport(catalog=catalog, collection_code=collection_code, oracle_env=oracle_env)
    try:
        print(f"[compare] Dumping Oracle for {catalog!r} ...", file=sys.stderr)
        report.oracle_doc = _oracle_dump(catalog, oracle_env)
    except RuntimeError as exc:
        report.error = f"Oracle dump failed: {exc}"
        return report

    try:
        print(f"[compare] Dumping Specify for {catalog!r} ({collection_code}) ...", file=sys.stderr)
        report.specify_doc = _specify_dump(catalog, collection_code)
    except RuntimeError as exc:
        report.error = f"Specify dump failed: {exc}"
        return report

    oracle = OracleAdapter(report.oracle_doc)
    specify = SpecifyAdapter(report.specify_doc)
    report.checks = run_checks(oracle, specify)
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare Oracle source data with Specify7 sink for migrated specimens.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src_grp = parser.add_mutually_exclusive_group(required=True)
    src_grp.add_argument("--fixture", "-f", metavar="YAML",
                         help="YAML file with fixture list (default: scripts/test_fixtures.yaml)")
    src_grp.add_argument("--catalog", "-c", metavar="CAT",
                         help="Single catalog number to compare")

    parser.add_argument("--collection-code", "-C", metavar="CODE", default="NHM-karplanter",
                        help="Specify collection code (used with --catalog; default: NHM-karplanter)")
    parser.add_argument("--oracle-env", default="prod", choices=["prod", "test"],
                        help="Oracle environment (default: prod)")
    parser.add_argument("--format", default="terminal",
                        choices=["terminal", "json", "md", "all"],
                        help="Output format (default: terminal)")
    parser.add_argument("--output-dir", "-o", metavar="DIR",
                        help="Write JSON/Markdown reports to this directory (implies --format all if not set)")
    args = parser.parse_args()

    # -- Resolve fixture list --
    fixtures: list[dict] = []
    if args.catalog:
        fixtures.append({
            "catalog": args.catalog,
            "collection_code": args.collection_code,
            "oracle_env": args.oracle_env,
        })
    else:
        fixture_path = Path(args.fixture)
        if not fixture_path.exists():
            sys.exit(f"Fixture file not found: {fixture_path}")
        if yaml is None:
            sys.exit("PyYAML not installed. Run: pip install pyyaml")
        with fixture_path.open() as fh:
            data = yaml.safe_load(fh)
        for entry in data.get("fixtures") or []:
            if entry.get("skip"):
                continue
            fixtures.append({
                "catalog": entry["catalog"],
                "collection_code": entry.get("collection_code", args.collection_code),
                "oracle_env": entry.get("oracle_env", args.oracle_env),
            })

    if not fixtures:
        sys.exit("No fixtures to process.")

    output_dir: Path | None = None
    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    fmt = args.format
    if output_dir and fmt == "terminal":
        fmt = "all"

    # -- Run each specimen --
    all_reports: list[SpecimenReport] = []
    exit_code = 0

    for fix in fixtures:
        cat = fix["catalog"]
        report = _run_specimen(cat, fix["collection_code"], fix["oracle_env"])
        all_reports.append(report)

        if report.error:
            print(f"\n[ERROR] {cat}: {report.error}", file=sys.stderr)
            exit_code = 1
            continue

        oracle = OracleAdapter(report.oracle_doc)
        specify = SpecifyAdapter(report.specify_doc)
        counts = _summary_counts(report.checks)
        if counts["FAIL"]:
            exit_code = 1

        if fmt in ("terminal", "all"):
            render_terminal(cat, report.checks)

        if fmt in ("json", "all"):
            json_report = render_json(cat, report.checks, report.oracle_doc, report.specify_doc)
            json_str = json.dumps(json_report, indent=2, ensure_ascii=False)
            if output_dir:
                out_path = output_dir / f"{cat.replace('/', '_')}_report.json"
                out_path.write_text(json_str, encoding="utf-8")
                print(f"[compare] Wrote {out_path}", file=sys.stderr)
            else:
                print(json_str)

        if fmt in ("md", "all"):
            md_str = render_markdown(cat, report.checks, oracle, specify, report.oracle_doc, report.specify_doc)
            if output_dir:
                out_path = output_dir / f"{cat.replace('/', '_')}_evidence.md"
                out_path.write_text(md_str, encoding="utf-8")
                print(f"[compare] Wrote {out_path}", file=sys.stderr)
            else:
                print(md_str)

    # -- Overall summary when processing multiple fixtures --
    if len(all_reports) > 1:
        total_pass = sum(_summary_counts(r.checks)["PASS"] for r in all_reports if not r.error)
        total_fail = sum(_summary_counts(r.checks)["FAIL"] for r in all_reports if not r.error)
        total_warn = sum(_summary_counts(r.checks)["WARN"] for r in all_reports if not r.error)
        errors = sum(1 for r in all_reports if r.error)
        print(
            f"\n{'═' * 50}\n"
            f"  Overall: {len(all_reports)} specimens  "
            f"pass={total_pass}  fail={total_fail}  warn={total_warn}  errors={errors}\n"
            f"{'═' * 50}\n",
            file=sys.stderr,
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
