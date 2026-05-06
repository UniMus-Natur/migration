#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import sys
import time
import traceback
import uuid
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import migration_compare as mc  # noqa: E402


HOST = os.getenv("HARNESS_HOST", "0.0.0.0")
PORT = int(os.getenv("HARNESS_PORT", "8088"))
BASE_PATH = (os.getenv("HARNESS_BASE_PATH", "/migration-harness") or "/migration-harness").rstrip("/")
DEFAULT_COLLECTION = os.getenv("HARNESS_DEFAULT_COLLECTION", "NHM-karplanter")
DEFAULT_ORACLE_ENV = os.getenv("HARNESS_DEFAULT_ORACLE_ENV", "prod")
REQUIRED_TOKEN = os.getenv("HARNESS_TOKEN", "")
MAX_STORED_RESULTS = int(os.getenv("HARNESS_MAX_STORED_RESULTS", "20"))
RESULTS: dict[str, dict] = {}


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
</ul>

<p><small>Sizes: Oracle {len(json.dumps(oracle_doc, ensure_ascii=False)):,} bytes, Specify {len(json.dumps(specify_doc, ensure_ascii=False)):,} bytes.</small></p>

<p><a href="{html.escape(BASE_PATH)}">Run another</a></p>
"""


def app(environ, start_response):
    path = (environ.get("PATH_INFO") or "").rstrip("/") or "/"
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if path == "/healthz":
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"ok"]

    if path == "/" and BASE_PATH != "":
        start_response("302 Found", [("Location", BASE_PATH)])
        return [b""]

    if path == BASE_PATH and method == "GET":
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [_html_page(_form())]

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

    if path.startswith(f"{BASE_PATH}/result/") and method == "GET":
        if not _is_authorized(environ):
            start_response("401 Unauthorized", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"unauthorized"]

        suffix = path[len(f"{BASE_PATH}/result/") :]
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
        if len(parts) == 2 and parts[1] in {"oracle.json", "specify.json", "checks.json"}:
            rid = parts[0]
            result = RESULTS.get(rid)
            if not result:
                start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
                return [b"result not found"]
            kind = parts[1]
            if kind == "oracle.json":
                obj = result["oracle"]
            elif kind == "specify.json":
                obj = result["specify"]
            else:
                obj = result["checks"]
            qs = parse_qs(environ.get("QUERY_STRING", ""))
            pretty = (qs.get("pretty") or ["0"])[0] in {"1", "true", "yes"}
            body = json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None).encode("utf-8")
            start_response("200 OK", [("Content-Type", "application/json; charset=utf-8")])
            return [body]

    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"not found"]


if __name__ == "__main__":
    print(f"migration-harness listening on {HOST}:{PORT} base={BASE_PATH}")
    with make_server(HOST, PORT, app) as srv:
        srv.serve_forever()

