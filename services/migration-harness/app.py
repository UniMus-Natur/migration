#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import sys
import time
import traceback
import uuid
from collections import defaultdict
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

try:
    from waitress import serve as _waitress_serve
except ImportError:
    _waitress_serve = None


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SPA_DIST = Path(__file__).resolve().parent.parent / "mapping-studio" / "dist"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import migration_compare as mc  # noqa: E402
from json_path_outline import build_path_outline, build_path_outline_bundle  # noqa: E402
from specify_schema_export import build_specify_schema  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST = os.getenv("HARNESS_HOST", "0.0.0.0")
PORT = int(os.getenv("HARNESS_PORT", "8088"))
WAITRESS_THREADS = int(os.getenv("HARNESS_WAITRESS_THREADS", "8"))
BASE_PATH = (os.getenv("HARNESS_BASE_PATH", "/migration-harness") or "/migration-harness").rstrip("/")
DEFAULT_COLLECTION = os.getenv("HARNESS_DEFAULT_COLLECTION", "NHM-karplanter")
DEFAULT_ORACLE_ENV = os.getenv("HARNESS_DEFAULT_ORACLE_ENV", "prod")
REQUIRED_TOKEN = os.getenv("HARNESS_TOKEN", "")
MAX_STORED_RESULTS = int(os.getenv("HARNESS_MAX_STORED_RESULTS", "20"))
# Leaf keys longer than this are masked in the value index (default: 2^16 - 1).
VALUE_INDEX_MAX_KEY_CHARS = int(os.getenv("HARNESS_VALUE_INDEX_MAX_KEY_CHARS", "65535"))

RESULTS: dict[str, dict] = {}

# In-process cache for specify schema (expensive DB query; schema rarely changes).
_SPECIFY_SCHEMA_CACHE: dict[str, Any] | None = None

# ---------------------------------------------------------------------------
# Value index helpers
# ---------------------------------------------------------------------------

def _mask_large_value_key(raw: str) -> str:
    """Avoid huge JSON/BLOB strings as map keys; keep short stable placeholder."""
    if VALUE_INDEX_MAX_KEY_CHARS <= 0 or len(raw) <= VALUE_INDEX_MAX_KEY_CHARS:
        return raw
    digest = hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"<BLOB len={len(raw)} md5={digest}>"


def _leaf_value_key(v: Any) -> str:
    """Stable string for deduplication keys (JSON leaves only)."""
    if v is None:
        return "<null>"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return _mask_large_value_key(v)
    if isinstance(v, bytes):
        return f"<bytes {len(v)}>"
    if isinstance(v, (list, dict)):
        dumped = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
        return _mask_large_value_key(dumped)
    return _mask_large_value_key(str(v))


def _index_leaf_values(obj: Any, path: str, acc: dict[str, list[str]]) -> None:
    """Map each leaf scalar to JSON-path-like strings where it appears."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            seg = str(k)
            if not seg.isidentifier():
                seg = "[" + json.dumps(seg, ensure_ascii=False) + "]"
                next_path = f"{path}{seg}" if path else seg.lstrip(".")
            else:
                next_path = f"{path}.{seg}" if path else seg
            _index_leaf_values(v, next_path, acc)
        return
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            next_path = f"{path}[{i}]"
            _index_leaf_values(v, next_path, acc)
        return
    key = _leaf_value_key(obj)
    acc[key].append(path if path else "$")


def build_value_index(doc: Any) -> dict[str, Any]:
    """Inverse view: deduplicated leaf values -> sorted list of paths."""
    acc: dict[str, list[str]] = defaultdict(list)
    _index_leaf_values(doc, "", acc)
    by_value = {k: sorted(set(paths)) for k, paths in sorted(acc.items(), key=lambda x: (-len(x[1]), x[0][:80]))}
    total_occ = sum(len(paths) for paths in by_value.values())
    max_paths = max((len(p) for p in by_value.values()), default=0)
    return {
        "schema": "migration-harness/value-index/v1",
        "by_value": by_value,
        "meta": {
            "unique_leaf_values": len(by_value),
            "total_leaf_occurrences": total_occ,
            "max_paths_for_one_value": max_paths,
            "max_key_chars_before_mask": VALUE_INDEX_MAX_KEY_CHARS,
        },
    }

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _html_page(body: str) -> bytes:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Migration Harness</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 20px; }}
    input, select, button {{ padding: 6px; margin: 4px 0; }}
    .row {{ margin-bottom: 12px; }}
    .ok {{ color: #0a7a22; }}
    .fail {{ color: #b00020; }}
    .warn {{ color: #9c6b00; }}
    pre {{ background: #f5f5f5; padding: 12px; overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; text-align: left; }}
    details {{ margin: 10px 0; }}
    .studio-btn {{
      display: inline-block; margin-top: 16px; padding: 10px 20px;
      background: #1a56db; color: #fff; border-radius: 6px;
      text-decoration: none; font-weight: 600; font-size: 15px;
    }}
  </style>
</head>
<body>
  {body}
</body>
</html>""".encode("utf-8")


def _form(error: str = "", default_catalog: str = "O-V-2000001") -> str:
    err = f'<p class="fail">{html.escape(error)}</p>' if error else ""
    return f"""
<h1>Migration Harness</h1>
<p>Run live Oracle + Specify extraction and compare results.</p>
{err}
<form method="post" action="{html.escape(BASE_PATH)}/run">
  <div class="row">
    <label>Token</label><br/>
    <input type="password" name="token" required />
  </div>
  <div class="row">
    <label>Catalog number</label><br/>
    <input type="text" name="catalog" value="{html.escape(default_catalog)}" required />
  </div>
  <div class="row">
    <label>Collection code</label><br/>
    <input type="text" name="collection_code" value="{html.escape(DEFAULT_COLLECTION)}" required />
  </div>
  <div class="row">
    <label>Oracle environment</label><br/>
    <select name="oracle_env">
      <option value="prod" {"selected" if DEFAULT_ORACLE_ENV == "prod" else ""}>prod</option>
      <option value="test" {"selected" if DEFAULT_ORACLE_ENV == "test" else ""}>test</option>
    </select>
  </div>
  <button type="submit">Run Compare</button>
</form>
"""


def _parse_cookies(environ) -> dict[str, str]:
    raw = environ.get("HTTP_COOKIE", "")
    if not raw:
        return {}
    c = SimpleCookie()
    c.load(raw)
    return {k: morsel.value for k, morsel in c.items()}


def _is_authorized(environ) -> bool:
    if not REQUIRED_TOKEN:
        return True
    cookies = _parse_cookies(environ)
    return cookies.get("harness_token") == REQUIRED_TOKEN


def _store_result(payload: dict) -> str:
    rid = uuid.uuid4().hex
    RESULTS[rid] = payload
    if len(RESULTS) > MAX_STORED_RESULTS:
        oldest = sorted(RESULTS.items(), key=lambda kv: kv[1].get("_created_at", 0))[: len(RESULTS) - MAX_STORED_RESULTS]
        for key, _ in oldest:
            RESULTS.pop(key, None)
    return rid


def _render_results(result_id: str, catalog: str, checks: list[mc.CheckResult], oracle_doc: dict, specify_doc: dict) -> str:
    counts = mc._summary_counts(checks)
    rows = []
    for c in checks:
        cls = "ok" if c.status == "PASS" else ("warn" if c.status == "WARN" else ("fail" if c.status == "FAIL" else ""))
        detail = c.detail or ""
        rows.append(
            f"<tr><td>{html.escape(c.name)}</td><td class='{cls}'>{html.escape(c.status)}</td>"
            f"<td>{html.escape(detail)}</td></tr>"
        )

    studio_url = f"{html.escape(BASE_PATH)}/explore?result={html.escape(result_id)}"

    return f"""
<h1>Migration Harness</h1>
<p><b>Catalog:</b> {html.escape(catalog)}</p>
<p>
  <b>Summary:</b>
  <span class="ok">pass={counts["PASS"]}</span>,
  <span class="fail">fail={counts["FAIL"]}</span>,
  <span class="warn">warn={counts["WARN"]}</span>,
  skip={counts["SKIP"]}
</p>

<a class="studio-btn" href="{studio_url}" target="_blank">Open in Mapping Studio &rarr;</a>

<table>
  <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
  <tbody>
    {''.join(rows)}
  </tbody>
</table>

<h3>Data exports</h3>
<ul>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/oracle.json?pretty=1" target="_blank">Open Oracle JSON (pretty)</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/specify.json?pretty=1" target="_blank">Open Specify JSON (pretty)</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/checks.json?pretty=1" target="_blank">Open Check Results JSON</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/oracle-values.json?pretty=1" target="_blank">Oracle value index (deduped values &rarr; paths)</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/specify-values.json?pretty=1" target="_blank">Specify value index (deduped values &rarr; paths)</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/value-index.json?pretty=1" target="_blank">Combined value index (both sides)</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/oracle-path-outline.json?pretty=1" target="_blank">Oracle path outline (trie)</a>
      &mdash; <a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/oracle-path-outline.json?pretty=1&amp;generalize=1" target="_blank">generalized</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/specify-path-outline.json?pretty=1" target="_blank">Specify path outline</a>
      &mdash; <a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/specify-path-outline.json?pretty=1&amp;generalize=1" target="_blank">generalized</a></li>
  <li><a href="{html.escape(BASE_PATH)}/result/{html.escape(result_id)}/path-outline.json?pretty=1" target="_blank">Combined path outline (both sides)</a></li>
</ul>

<p><small>Sizes: Oracle {len(json.dumps(oracle_doc, ensure_ascii=False)):,} bytes, Specify {len(json.dumps(specify_doc, ensure_ascii=False)):,} bytes.</small></p>

<p><a href="{html.escape(BASE_PATH)}">Run another</a></p>
"""

# ---------------------------------------------------------------------------
# Static file serving for the Mapping Studio SPA
# ---------------------------------------------------------------------------

_MIME_FALLBACK = "application/octet-stream"
_MIME_MAP = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".map": "application/json",
}


def _serve_static(environ, start_response, rel_path: str):
    """Serve a file from SPA_DIST; rel_path is relative to dist root."""
    if not SPA_DIST.exists():
        start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
        return [b"Mapping Studio not built yet (dist/ missing)"]

    # Prevent directory traversal.
    target = (SPA_DIST / rel_path.lstrip("/")).resolve()
    try:
        target.relative_to(SPA_DIST.resolve())
    except ValueError:
        start_response("403 Forbidden", [("Content-Type", "text/plain")])
        return [b"Forbidden"]

    # For non-asset paths (e.g. deep links into the SPA) serve index.html.
    if not target.exists() or target.is_dir():
        target = SPA_DIST / "index.html"

    if not target.exists():
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]

    suffix = target.suffix.lower()
    content_type = _MIME_MAP.get(suffix, mimetypes.types_map.get(suffix, _MIME_FALLBACK))
    data = target.read_bytes()
    etag = f'"{hashlib.md5(data).hexdigest()}"'
    if environ.get("HTTP_IF_NONE_MATCH") == etag:
        start_response("304 Not Modified", [("ETag", etag)])
        return [b""]
    start_response("200 OK", [
        ("Content-Type", content_type),
        ("Content-Length", str(len(data))),
        ("ETag", etag),
        ("Cache-Control", "public, max-age=3600"),
    ])
    return [data]

# ---------------------------------------------------------------------------
# Specify schema: cached live query
# ---------------------------------------------------------------------------

def _get_specify_schema() -> dict[str, Any]:
    global _SPECIFY_SCHEMA_CACHE
    if _SPECIFY_SCHEMA_CACHE is not None:
        return _SPECIFY_SCHEMA_CACHE
    import specify_schema_export as sse  # noqa: PLC0415
    cfg = sse._db_config()
    _SPECIFY_SCHEMA_CACHE = sse.build_specify_schema(cfg)
    return _SPECIFY_SCHEMA_CACHE

# ---------------------------------------------------------------------------
# WSGI application
# ---------------------------------------------------------------------------

def app(environ, start_response):
    path = (environ.get("PATH_INFO") or "").rstrip("/") or "/"
    method = environ.get("REQUEST_METHOD", "GET").upper()

    # Health probe (no auth, no base-path prefix needed).
    if path == "/healthz":
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"ok"]

    # Root redirect.
    if path == "/" and BASE_PATH != "":
        start_response("302 Found", [("Location", BASE_PATH)])
        return [b""]

    # ---- Harness home form ------------------------------------------------
    if path == BASE_PATH and method == "GET":
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [_html_page(_form())]

    # ---- Compare run (POST) -----------------------------------------------
    if path == f"{BASE_PATH}/run" and method == "POST":
        try:
            size = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError:
            size = 0
        raw = environ["wsgi.input"].read(size).decode("utf-8", errors="replace")
        form = parse_qs(raw)

        token = (form.get("token") or [""])[0]
        catalog = (form.get("catalog") or [""])[0].strip()
        collection_code = (form.get("collection_code") or [DEFAULT_COLLECTION])[0].strip() or DEFAULT_COLLECTION
        oracle_env = (form.get("oracle_env") or [DEFAULT_ORACLE_ENV])[0].strip() or DEFAULT_ORACLE_ENV

        if REQUIRED_TOKEN and token != REQUIRED_TOKEN:
            start_response("401 Unauthorized", [("Content-Type", "text/html; charset=utf-8")])
            return [_html_page(_form(error="Invalid token.", default_catalog=catalog or "O-V-2000001"))]
        if not catalog:
            start_response("400 Bad Request", [("Content-Type", "text/html; charset=utf-8")])
            return [_html_page(_form(error="Catalog number is required."))]

        try:
            oracle_doc = mc._oracle_dump(catalog, oracle_env)
            specify_doc = mc._specify_dump(catalog, collection_code)
            checks = mc.run_checks(mc.OracleAdapter(oracle_doc), mc.SpecifyAdapter(specify_doc))
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            body = _form(error=err, default_catalog=catalog)
            body += "<details><summary>Traceback</summary><pre>" + html.escape(traceback.format_exc()) + "</pre></details>"
            start_response("500 Internal Server Error", [("Content-Type", "text/html; charset=utf-8")])
            return [_html_page(body)]

        payload = {
            "_created_at": time.time(),
            "catalog": catalog,
            "oracle": oracle_doc,
            "specify": specify_doc,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "detail": c.detail,
                    "oracle_val": c.oracle_val,
                    "specify_val": c.specify_val,
                }
                for c in checks
            ],
        }
        result_id = _store_result(payload)
        headers = [("Content-Type", "text/html; charset=utf-8")]
        if REQUIRED_TOKEN:
            headers.append(("Set-Cookie", f"harness_token={token}; Path={BASE_PATH}; HttpOnly; SameSite=Lax"))
        start_response("200 OK", headers)
        return [_html_page(_render_results(result_id, catalog, checks, oracle_doc, specify_doc))]

    # ---- Specify schema API -----------------------------------------------
    if path == f"{BASE_PATH}/api/specify-schema" and method == "GET":
        try:
            schema = _get_specify_schema()
        except Exception as exc:  # noqa: BLE001
            start_response("502 Bad Gateway", [("Content-Type", "application/json")])
            return [json.dumps({"error": str(exc)}).encode("utf-8")]
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        pretty = (qs.get("pretty") or ["0"])[0] in {"1", "true", "yes"}
        # Allow SPA (same origin) to always get fresh schema on force refresh.
        force = (qs.get("force") or ["0"])[0] in {"1", "true", "yes"}
        if force:
            global _SPECIFY_SCHEMA_CACHE
            _SPECIFY_SCHEMA_CACHE = None
            schema = _get_specify_schema()
        body = json.dumps(schema, indent=2 if pretty else None, ensure_ascii=False).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Access-Control-Allow-Origin", "*"),
        ])
        return [body]

    # ---- Per-result JSON exports ------------------------------------------
    if path.startswith(f"{BASE_PATH}/result/") and method == "GET":
        if not _is_authorized(environ):
            start_response("401 Unauthorized", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"unauthorized"]

        suffix = path[len(f"{BASE_PATH}/result/"):]
        parts = [p for p in suffix.split("/") if p]

        if len(parts) == 1:
            rid = parts[0]
            result = RESULTS.get(rid)
            if not result:
                start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
                return [b"result not found"]
            checks = [mc.CheckResult(name=c["name"], status=c["status"], detail=c.get("detail", "")) for c in result["checks"]]
            start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
            return [_html_page(_render_results(rid, result["catalog"], checks, result["oracle"], result["specify"]))]

        _RESULT_KINDS = {
            "oracle.json",
            "specify.json",
            "checks.json",
            "oracle-values.json",
            "specify-values.json",
            "value-index.json",
            "oracle-path-outline.json",
            "specify-path-outline.json",
            "path-outline.json",
        }
        if len(parts) == 2 and parts[1] in _RESULT_KINDS:
            rid = parts[0]
            result = RESULTS.get(rid)
            if not result:
                start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
                return [b"result not found"]
            kind = parts[1]
            qs = parse_qs(environ.get("QUERY_STRING", ""))
            pretty = (qs.get("pretty") or ["0"])[0] in {"1", "true", "yes"}
            generalize = (qs.get("generalize") or ["0"])[0] in {"1", "true", "yes"}

            if kind == "oracle.json":
                obj = result["oracle"]
            elif kind == "specify.json":
                obj = result["specify"]
            elif kind == "checks.json":
                obj = result["checks"]
            elif kind == "oracle-values.json":
                obj = build_value_index(result["oracle"])
            elif kind == "specify-values.json":
                obj = build_value_index(result["specify"])
            elif kind == "oracle-path-outline.json":
                obj = build_path_outline(result["oracle"], generalize_array_indices=generalize)
            elif kind == "specify-path-outline.json":
                obj = build_path_outline(result["specify"], generalize_array_indices=generalize)
            elif kind == "path-outline.json":
                obj = build_path_outline_bundle(
                    result["oracle"],
                    result["specify"],
                    catalog=result.get("catalog"),
                    generalize_array_indices=generalize,
                )
            else:
                obj = {
                    "schema": "migration-harness/value-index-bundle/v1",
                    "catalog": result.get("catalog"),
                    "oracle": build_value_index(result["oracle"]),
                    "specify": build_value_index(result["specify"]),
                }

            body = json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None).encode("utf-8")
            start_response("200 OK", [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Access-Control-Allow-Origin", "*"),
            ])
            return [body]

    # ---- Mapping Studio SPA (static files) --------------------------------
    explore_prefix = f"{BASE_PATH}/explore"
    if path == explore_prefix or path.startswith(explore_prefix + "/"):
        rel = path[len(explore_prefix):]  # "" or "/assets/foo.js"
        if not rel:
            rel = "/"
        return _serve_static(environ, start_response, rel)

    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"not found"]


if __name__ == "__main__":
    print(f"migration-harness listening on {HOST}:{PORT} base={BASE_PATH}")
    if _waitress_serve is not None:
        # Threaded: wsgiref blocks all clients on one long /run request (504s on /).
        _waitress_serve(app, host=HOST, port=PORT, threads=max(1, WAITRESS_THREADS))
    else:
        with make_server(HOST, PORT, app) as srv:
            srv.serve_forever()
