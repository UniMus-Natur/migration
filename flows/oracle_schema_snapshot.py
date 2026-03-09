import csv
import json
import os
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger
from prefect.runtime import flow_run

from flows.lib.oracle_connectivity import create_oracle_connection, get_oracle_config_from_env
from flows.lib.s3_connectivity import upload_file_with_compat_retry


def _parse_owners(owners_csv: str | None) -> list[str]:
    if owners_csv:
        owners = [owner.strip().upper() for owner in owners_csv.split(",") if owner.strip()]
        if owners:
            return owners

    env_owners = os.getenv("ORACLE_SCHEMA_OWNERS", "")
    owners = [owner.strip().upper() for owner in env_owners.split(",") if owner.strip()]
    if owners:
        return owners

    default_owner = os.getenv("ORACLE_PROD_USER", "").strip().upper()
    return [default_owner] if default_owner else []


def _build_owner_clause(column: str, owners: list[str]) -> tuple[str, dict[str, str]]:
    if not owners:
        return "", {}
    bind_map = {f"owner_{idx}": owner for idx, owner in enumerate(owners)}
    bind_keys = ", ".join(f":{key}" for key in bind_map)
    return f" WHERE {column} IN ({bind_keys})", bind_map


def _query_rows(connection, sql: str, params: dict | None = None) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or {})
        columns = [desc[0].lower() for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _write_csv(path: Path, rows: list[dict], headers: Iterable[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(headers))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_dbml(
    tables: list[dict],
    columns: list[dict],
    constraints: list[dict],
    constraint_columns: list[dict],
) -> str:
    pk_by_table: dict[tuple[str, str], set[str]] = {}
    unique_by_table: dict[tuple[str, str], set[str]] = {}
    fk_rows: list[dict] = []

    for cons in constraints:
        key = (cons["owner"], cons["table_name"])
        ctype = cons.get("constraint_type")
        if ctype == "P":
            pk_by_table.setdefault(key, set())
        elif ctype == "U":
            unique_by_table.setdefault(key, set())
        elif ctype == "R":
            fk_rows.append(cons)

    columns_by_constraint: dict[tuple[str, str, str], list[dict]] = {}
    columns_by_owner_constraint: dict[tuple[str, str], list[dict]] = {}
    for row in constraint_columns:
        ckey = (row["owner"], row["table_name"], row["constraint_name"])
        columns_by_constraint.setdefault(ckey, []).append(row)
        okey = (row["owner"], row["constraint_name"])
        columns_by_owner_constraint.setdefault(okey, []).append(row)
    for ckey in columns_by_constraint:
        columns_by_constraint[ckey] = sorted(
            columns_by_constraint[ckey], key=lambda r: r.get("position") or 0
        )
    for okey in columns_by_owner_constraint:
        columns_by_owner_constraint[okey] = sorted(
            columns_by_owner_constraint[okey], key=lambda r: r.get("position") or 0
        )

    for cons in constraints:
        owner = cons["owner"]
        table = cons["table_name"]
        cname = cons["constraint_name"]
        ctype = cons.get("constraint_type")
        ccols = columns_by_constraint.get((owner, table, cname), [])
        if ctype == "P":
            pk_by_table.setdefault((owner, table), set()).update(
                col["column_name"] for col in ccols
            )
        elif ctype == "U":
            unique_by_table.setdefault((owner, table), set()).update(
                col["column_name"] for col in ccols
            )

    cols_by_table: dict[tuple[str, str], list[dict]] = {}
    for col in columns:
        tkey = (col["owner"], col["table_name"])
        cols_by_table.setdefault(tkey, []).append(col)
    for tkey in cols_by_table:
        cols_by_table[tkey] = sorted(cols_by_table[tkey], key=lambda r: r["column_id"])

    lines: list[str] = []
    lines.append("// Generated from Oracle ALL_* metadata")
    lines.append("")

    for table in tables:
        owner = table["owner"]
        table_name = table["table_name"]
        tkey = (owner, table_name)
        lines.append(f"Table {owner}.{table_name} {{")
        for col in cols_by_table.get(tkey, []):
            attrs: list[str] = []
            if col["column_name"] in pk_by_table.get(tkey, set()):
                attrs.append("pk")
            if col.get("nullable") == "N":
                attrs.append("not null")
            if col["column_name"] in unique_by_table.get(tkey, set()):
                attrs.append("unique")
            attrs_str = f" [{', '.join(attrs)}]" if attrs else ""
            lines.append(f"  {col['column_name']} {col['data_type']}{attrs_str}")
        lines.append("}")
        lines.append("")

    for fk in fk_rows:
        owner = fk["owner"]
        table = fk["table_name"]
        cname = fk["constraint_name"]
        ccols = columns_by_constraint.get((owner, table, cname), [])
        ref_owner = fk.get("r_owner")
        ref_cname = fk.get("r_constraint_name")
        ref_cols = columns_by_owner_constraint.get((ref_owner, ref_cname), [])
        if not ccols or not ref_cols:
            continue
        # Keep a one-column reference per line for broad DBML tooling compatibility.
        for src, dst in zip(ccols, ref_cols):
            lines.append(
                f"Ref: {owner}.{table}.{src['column_name']} > "
                f"{ref_owner}.{dst['table_name']}.{dst['column_name']}"
            )

    lines.append("")
    return "\n".join(lines)


@flow(
    name="Oracle Schema Snapshot",
    description="Extracts Oracle PROD schema metadata and uploads machine-readable outputs to S3",
)
def oracle_schema_snapshot_flow(owners_csv: str | None = None):
    logger = get_run_logger()
    owners = _parse_owners(owners_csv)
    logger.info(f"Schema owners filter: {owners if owners else 'ALL accessible owners'}")

    config = get_oracle_config_from_env("PROD")
    connection = create_oracle_connection(config)
    try:
        table_where, table_params = _build_owner_clause("owner", owners)
        col_where, col_params = _build_owner_clause("owner", owners)
        cons_where, cons_params = _build_owner_clause("owner", owners)
        idx_where, idx_params = _build_owner_clause("owner", owners)
        idx_owner_where, idx_owner_params = _build_owner_clause("index_owner", owners)
        view_where, view_params = _build_owner_clause("owner", owners)

        tables = _query_rows(
            connection,
            f"""
            SELECT owner, table_name, tablespace_name, num_rows, last_analyzed
            FROM all_tables
            {table_where}
            ORDER BY owner, table_name
            """,
            table_params,
        )
        columns = _query_rows(
            connection,
            f"""
            SELECT owner, table_name, column_name, data_type, data_length, data_precision,
                   data_scale, nullable, column_id, char_used, char_length
            FROM all_tab_columns
            {col_where}
            ORDER BY owner, table_name, column_id
            """,
            col_params,
        )
        constraints = _query_rows(
            connection,
            f"""
            SELECT owner, table_name, constraint_name, constraint_type, r_owner,
                   r_constraint_name, status, validated
            FROM all_constraints
            {cons_where}
            ORDER BY owner, table_name, constraint_name
            """,
            cons_params,
        )
        constraint_columns = _query_rows(
            connection,
            f"""
            SELECT owner, table_name, constraint_name, column_name, position
            FROM all_cons_columns
            {cons_where}
            ORDER BY owner, table_name, constraint_name, position
            """,
            cons_params,
        )
        indexes = _query_rows(
            connection,
            f"""
            SELECT owner, table_name, index_name, uniqueness, index_type, status
            FROM all_indexes
            {idx_where}
            ORDER BY owner, table_name, index_name
            """,
            idx_params,
        )
        index_columns = _query_rows(
            connection,
            f"""
            SELECT index_owner AS owner, table_name, index_name, column_name, column_position, descend
            FROM all_ind_columns
            {idx_owner_where}
            ORDER BY owner, table_name, index_name, column_position
            """,
            idx_owner_params,
        )
        views = _query_rows(
            connection,
            f"""
            SELECT owner, view_name, text_length
            FROM all_views
            {view_where}
            ORDER BY owner, view_name
            """,
            view_params,
        )
    finally:
        connection.close()

    snapshot_time = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = {
        "generated_at_utc": snapshot_time,
        "source": {
            "host": config.host,
            "port": config.port,
            "service_name": config.service_name,
            "owners_filter": owners,
        },
        "counts": {
            "tables": len(tables),
            "columns": len(columns),
            "constraints": len(constraints),
            "constraint_columns": len(constraint_columns),
            "indexes": len(indexes),
            "index_columns": len(index_columns),
            "views": len(views),
        },
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "constraint_columns": constraint_columns,
        "indexes": indexes,
        "index_columns": index_columns,
        "views": views,
    }

    with tempfile.TemporaryDirectory(prefix="oracle-schema-") as temp_dir:
        out_dir = Path(temp_dir)
        json_path = out_dir / "schema_catalog.json"
        tables_csv = out_dir / "tables.csv"
        columns_csv = out_dir / "columns.csv"
        constraints_csv = out_dir / "constraints.csv"
        indexes_csv = out_dir / "indexes.csv"
        views_csv = out_dir / "views.csv"
        dbml_path = out_dir / "schema.dbml"

        json_path.write_text(json.dumps(snapshot, default=str, indent=2), encoding="utf-8")
        _write_csv(
            tables_csv,
            tables,
            ["owner", "table_name", "tablespace_name", "num_rows", "last_analyzed"],
        )
        _write_csv(
            columns_csv,
            columns,
            [
                "owner",
                "table_name",
                "column_name",
                "data_type",
                "data_length",
                "data_precision",
                "data_scale",
                "nullable",
                "column_id",
                "char_used",
                "char_length",
            ],
        )
        _write_csv(
            constraints_csv,
            constraints,
            [
                "owner",
                "table_name",
                "constraint_name",
                "constraint_type",
                "r_owner",
                "r_constraint_name",
                "status",
                "validated",
            ],
        )
        _write_csv(
            indexes_csv,
            indexes,
            ["owner", "table_name", "index_name", "uniqueness", "index_type", "status"],
        )
        _write_csv(views_csv, views, ["owner", "view_name", "text_length"])
        dbml_path.write_text(
            _build_dbml(tables, columns, constraints, constraint_columns),
            encoding="utf-8",
        )

        bucket = os.getenv("S3_BUCKET")
        if not bucket:
            raise ValueError("Missing required S3_BUCKET environment variable")
        prefix = os.getenv("S3_PREFIX", "oracle-schema").strip("/")
        run_id = flow_run.id or snapshot_time
        base_key = f"{prefix}/{run_id}"

        upload_targets = {
            "schema_catalog.json": json_path,
            "tables.csv": tables_csv,
            "columns.csv": columns_csv,
            "constraints.csv": constraints_csv,
            "indexes.csv": indexes_csv,
            "views.csv": views_csv,
            "schema.dbml": dbml_path,
        }
        uploaded = []
        for filename, local_path in upload_targets.items():
            object_key = f"{base_key}/{filename}"
            upload_file_with_compat_retry(str(local_path), bucket, object_key)
            uploaded.append(f"s3://{bucket}/{object_key}")

        logger.info("Uploaded schema snapshot artifacts:")
        for uri in uploaded:
            logger.info(uri)

        return {"uploaded": uploaded, "counts": snapshot["counts"]}
