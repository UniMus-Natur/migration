#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import sys
import traceback
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


def _render_results(catalog: str, checks: list[mc.CheckResult], oracle_doc: dict, specify_doc: dict) -> str:
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

<details>
  <summary>Oracle JSON</summary>
  <pre>{html.escape(json.dumps(oracle_doc, indent=2, ensure_ascii=False))}</pre>
</details>

<details>
  <summary>Specify JSON</summary>
  <pre>{html.escape(json.dumps(specify_doc, indent=2, ensure_ascii=False))}</pre>
</details>

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

        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [_html_page(_render_results(catalog, checks, oracle_doc, specify_doc))]

    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"not found"]


if __name__ == "__main__":
    print(f"migration-harness listening on {HOST}:{PORT} base={BASE_PATH}")
    with make_server(HOST, PORT, app) as srv:
        srv.serve_forever()

