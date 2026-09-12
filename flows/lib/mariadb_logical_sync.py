"""Logical MariaDB dump/restore + Specify schema compatibility gates."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _mysql_module():
    try:
        import MySQLdb as mod  # type: ignore
        return mod
    except ImportError:
        import pymysql as mod

        return mod


@dataclass(frozen=True)
class MariaDBEndpoint:
    host: str
    port: int
    database: str
    user: str
    password: str
    label: str = "db"

    def identity(self) -> tuple[str, int, str]:
        return (self.host.lower().strip(), int(self.port), self.database.strip())


@dataclass(frozen=True)
class SpVersionInfo:
    app_version: str
    schema_version: str
    workbench_schema_version: str
    row_count: int

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.app_version, self.schema_version, self.workbench_schema_version)


@dataclass(frozen=True)
class CompatibilityReport:
    source_version: SpVersionInfo
    target_version: SpVersionInfo
    source_schema_fingerprint: str
    target_schema_fingerprint: str
    source_table_count: int
    target_table_count: int
    source_approx_data_bytes: int
    target_approx_data_bytes: int
    versions_match: bool
    schemas_match: bool

    @property
    def compatible(self) -> bool:
        return self.versions_match and self.schemas_match


def staging_endpoint_from_env() -> MariaDBEndpoint:
    """In-cluster staging Specify DB (``DB_*``, with ``DATABASE_*`` fallbacks)."""
    host = (
        os.environ.get("DB_HOST")
        or os.environ.get("DATABASE_HOST")
        or "specify7-mariadb"
    ).strip()
    port = int(os.environ.get("DB_PORT") or os.environ.get("DATABASE_PORT") or "3306")
    database = (
        os.environ.get("DB_NAME") or os.environ.get("DATABASE_NAME") or "specify"
    ).strip()
    user = (
        os.environ.get("DB_USER")
        or os.environ.get("APP_USER_NAME")
        or "bn_specify"
    ).strip()
    password = (
        os.environ.get("DB_PASSWORD")
        or os.environ.get("APP_USER_PASSWORD")
        or ""
    )
    if not password:
        raise RuntimeError("Staging DB password missing (DB_PASSWORD / APP_USER_PASSWORD)")
    return MariaDBEndpoint(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        label="staging",
    )


def test_endpoint_via_tunnel(*, local_port: int) -> MariaDBEndpoint:
    """Test DB as reached through the SSH LocalForward on localhost."""
    database = (os.environ.get("TEST_DB_NAME") or "").strip()
    user = (os.environ.get("TEST_DB_USER") or "").strip()
    password = os.environ.get("TEST_DB_PASSWORD") or ""
    if not database:
        raise RuntimeError("TEST_DB_NAME is required")
    if not user:
        raise RuntimeError("TEST_DB_USER is required")
    if not password:
        raise RuntimeError("TEST_DB_PASSWORD is required")
    return MariaDBEndpoint(
        host="127.0.0.1",
        port=int(local_port),
        database=database,
        user=user,
        password=password,
        label="test",
    )


def assert_not_same_endpoint(source: MariaDBEndpoint, *, test_remote_host: str, test_remote_port: int, test_db_name: str) -> None:
    """Refuse sync when staging points at the same DB the bastion would reach."""
    src = source.identity()
    tgt = (test_remote_host.lower().strip(), int(test_remote_port), test_db_name.strip())
    if src == tgt:
        raise RuntimeError(
            f"Refusing sync: source {src!r} matches test remote identity {tgt!r}"
        )


def _connect(ep: MariaDBEndpoint):
    MySQLdb = _mysql_module()
    return MySQLdb.connect(
        host=ep.host,
        port=ep.port,
        user=ep.user,
        password=ep.password,
        database=ep.database,
        charset="utf8mb4",
        connect_timeout=30,
    )


def read_spversion(ep: MariaDBEndpoint) -> SpVersionInfo:
    with _connect(ep) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT AppVersion, SchemaVersion, WorkbenchSchemaVersion "
                "FROM spversion ORDER BY SpVersionID"
            )
            rows = cur.fetchall()
    if not rows:
        raise RuntimeError(f"{ep.label}: spversion has no rows")
    if len(rows) != 1:
        logger.warning("%s: spversion has %s rows; using first", ep.label, len(rows))
    app, schema, wb = rows[0]
    return SpVersionInfo(
        app_version=str(app or "").strip(),
        schema_version=str(schema or "").strip(),
        workbench_schema_version=str(wb or "").strip(),
        row_count=len(rows),
    )


def schema_fingerprint(ep: MariaDBEndpoint) -> str:
    """SHA-256 of ordered information_schema.COLUMNS for ``ep.database``."""
    sql = """
    SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA,
           IFNULL(COLUMN_DEFAULT, '<NULL>')
      FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = %s
     ORDER BY TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME
    """
    with _connect(ep) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [ep.database])
            rows = cur.fetchall()
    h = hashlib.sha256()
    for row in rows:
        line = "\t".join("" if c is None else str(c) for c in row) + "\n"
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def schema_stats(ep: MariaDBEndpoint) -> tuple[int, int]:
    """Return ``(table_count, approx_data_bytes)`` from ``information_schema.TABLES``."""
    with _connect(ep) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0)
                  FROM information_schema.TABLES
                 WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
                """,
                [ep.database],
            )
            count, nbytes = cur.fetchone()
    return int(count or 0), int(nbytes or 0)


def build_compatibility_report(
    source: MariaDBEndpoint,
    target: MariaDBEndpoint,
) -> CompatibilityReport:
    src_ver = read_spversion(source)
    tgt_ver = read_spversion(target)
    src_fp = schema_fingerprint(source)
    tgt_fp = schema_fingerprint(target)
    src_tables, src_bytes = schema_stats(source)
    tgt_tables, tgt_bytes = schema_stats(target)
    return CompatibilityReport(
        source_version=src_ver,
        target_version=tgt_ver,
        source_schema_fingerprint=src_fp,
        target_schema_fingerprint=tgt_fp,
        source_table_count=src_tables,
        target_table_count=tgt_tables,
        source_approx_data_bytes=src_bytes,
        target_approx_data_bytes=tgt_bytes,
        versions_match=src_ver.as_tuple() == tgt_ver.as_tuple(),
        schemas_match=src_fp == tgt_fp,
    )


def compatibility_report_dict(report: CompatibilityReport) -> dict[str, Any]:
    d = asdict(report)
    d["compatible"] = report.compatible
    return d


def assert_compatible(report: CompatibilityReport) -> None:
    if report.compatible:
        return
    parts: list[str] = []
    if not report.versions_match:
        parts.append(
            f"spversion mismatch source={report.source_version.as_tuple()!r} "
            f"target={report.target_version.as_tuple()!r}"
        )
    if not report.schemas_match:
        parts.append(
            f"schema fingerprint mismatch source={report.source_schema_fingerprint} "
            f"target={report.target_schema_fingerprint}"
        )
    raise RuntimeError("Compatibility gate failed: " + "; ".join(parts))


def _dump_cmd(ep: MariaDBEndpoint) -> list[str]:
    dump = shutil.which("mariadb-dump") or shutil.which("mysqldump")
    if not dump:
        raise RuntimeError("mariadb-dump/mysqldump not found (install mariadb-client)")
    return [
        dump,
        f"--host={ep.host}",
        f"--port={ep.port}",
        f"--user={ep.user}",
        f"--password={ep.password}",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--hex-blob",
        "--add-drop-table",
        "--default-character-set=utf8mb4",
        "--databases",
        ep.database,
    ]


def _mysql_cmd(ep: MariaDBEndpoint) -> list[str]:
    client = shutil.which("mariadb") or shutil.which("mysql")
    if not client:
        raise RuntimeError("mariadb/mysql client not found (install mariadb-client)")
    return [
        client,
        f"--host={ep.host}",
        f"--port={ep.port}",
        f"--user={ep.user}",
        f"--password={ep.password}",
        "--default-character-set=utf8mb4",
    ]


def stream_dump_restore(*, source: MariaDBEndpoint, target: MariaDBEndpoint) -> dict[str, Any]:
    """Pipe ``mysqldump`` from ``source`` into ``mysql`` on ``target`` (destructive replace)."""
    dump_cmd = _dump_cmd(source)
    restore_cmd = _mysql_cmd(target)
    logger.info(
        "Streaming logical dump %s:%s/%s → %s:%s/%s",
        source.host,
        source.port,
        source.database,
        target.host,
        target.port,
        target.database,
    )
    # Avoid logging passwords: use env MYSQL_PWD for children instead of --password in argv when possible.
    dump_env = os.environ.copy()
    restore_env = os.environ.copy()
    dump_env["MYSQL_PWD"] = source.password
    restore_env["MYSQL_PWD"] = target.password
    # Strip --password= from argv now that MYSQL_PWD is set
    dump_cmd = [c for c in dump_cmd if not c.startswith("--password=")]
    restore_cmd = [c for c in restore_cmd if not c.startswith("--password=")]

    dump_proc = subprocess.Popen(
        dump_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dump_env,
    )
    assert dump_proc.stdout is not None
    restore_proc = subprocess.Popen(
        restore_cmd,
        stdin=dump_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=restore_env,
    )
    dump_proc.stdout.close()  # allow dump to receive SIGPIPE if restore exits
    restore_stdout, restore_stderr = restore_proc.communicate()
    dump_stderr = dump_proc.stderr.read() if dump_proc.stderr else b""
    dump_code = dump_proc.wait()

    out: dict[str, Any] = {
        "dump_exit_code": dump_code,
        "restore_exit_code": restore_proc.returncode,
        "dump_stderr_tail": dump_stderr.decode("utf-8", errors="replace")[-4000:],
        "restore_stderr_tail": restore_stderr.decode("utf-8", errors="replace")[-4000:],
        "restore_stdout_tail": restore_stdout.decode("utf-8", errors="replace")[-1000:],
    }
    if dump_code != 0:
        raise RuntimeError(
            f"mysqldump failed (exit {dump_code}): {out['dump_stderr_tail']}"
        )
    if restore_proc.returncode != 0:
        raise RuntimeError(
            f"mysql restore failed (exit {restore_proc.returncode}): {out['restore_stderr_tail']}"
        )
    out["message"] = "dump|restore completed"
    return out
