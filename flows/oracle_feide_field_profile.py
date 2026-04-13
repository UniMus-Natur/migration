import csv
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger

from flows.lib.oracle_connectivity import create_oracle_connection, get_oracle_config_from_env
from flows.lib.s3_connectivity import upload_file_with_compat_retry


def _one_row(connection, sql: str, params: dict | None = None) -> tuple:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or {})
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Expected a row but query returned none")
        return row


def _many_rows(connection, sql: str, params: dict | None = None) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or {})
        return cursor.fetchall()


def _query_dict_rows(connection, sql: str, params: dict | None = None) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or {})
        columns = [desc[0].lower() for desc in cursor.description]
        rows = []
        for raw_row in cursor.fetchall():
            row = {}
            for key, value in zip(columns, raw_row):
                row[key] = value.isoformat() if hasattr(value, "isoformat") else value
            rows.append(row)
        return rows


def _write_csv_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _build_markdown_report(profile: dict) -> str:
    lines = [
        "# FEIDE Field Profile",
        "",
        "Source table: `USD_METADATA.BRUKARAR`",
        "Source field: `FEIDE`",
        "",
        f"- Generated at (UTC): `{profile['generated_at_utc']}`",
        f"- Environment prefix: `{profile['source']['env_prefix']}`",
        f"- Oracle source: `{profile['source']['host']}:{profile['source']['port']}/{profile['source']['service_name']}`",
        "",
        "## Population",
        "",
        f"- Total rows: `{profile['counts']['total_rows']}`",
        f"- Null rows: `{profile['counts']['null_rows']}`",
        f"- Blank-or-null rows: `{profile['counts']['blank_or_null_rows']}`",
        f"- Non-blank rows: `{profile['counts']['non_blank_rows']}`",
        f"- Distinct non-blank values: `{profile['counts']['distinct_non_blank']}`",
        f"- Distinct FEIDE values with duplicates: `{profile['counts']['duplicate_value_count']}`",
        "",
        "## Format Heuristics (non-blank values)",
        "",
        f"- Contains `@`: `{profile['format_heuristics']['has_at']}`",
        f"- Email-like (`local@domain`): `{profile['format_heuristics']['email_like']}`",
        f"- Numeric-only: `{profile['format_heuristics']['numeric_only']}`",
        f"- Contains whitespace: `{profile['format_heuristics']['contains_space']}`",
        "",
        "## Top Email Domains",
        "",
    ]
    if profile["top_email_domains"]:
        for item in profile["top_email_domains"]:
            lines.append(f"- `{item['domain']}`: `{item['count']}`")
    else:
        lines.append("- No email-like FEIDE values found.")
    lines.append("")
    lines.append("Additional artifacts include row-level exports for authorized private analysis.")
    return "\n".join(lines)


@flow(
    name="Oracle FEIDE Field Profile",
    description="Profiles USD_METADATA.BRUKARAR.FEIDE and uploads aggregate and row-level results to S3",
)
def oracle_feide_field_profile_flow(name_contains: str | None = None) -> dict:
    logger = get_run_logger()
    config = get_oracle_config_from_env("PROD")
    connection = create_oracle_connection(config)
    name_contains = (name_contains or "").strip()
    use_name_filter = len(name_contains) > 0
    name_filter_sql = (
        "AND LOWER(NAMN) LIKE LOWER(:name_like)"
        if use_name_filter
        else ""
    )
    params = {"name_like": f"%{name_contains}%"} if use_name_filter else {}

    try:
        (
            total_rows,
            null_rows,
            blank_or_null_rows,
            non_blank_rows,
            distinct_non_blank,
        ) = _one_row(
            connection,
            f"""
            SELECT
              COUNT(*) AS total_rows,
              SUM(CASE WHEN FEIDE IS NULL THEN 1 ELSE 0 END) AS null_rows,
              SUM(CASE WHEN TRIM(FEIDE) IS NULL THEN 1 ELSE 0 END) AS blank_or_null_rows,
              SUM(CASE WHEN TRIM(FEIDE) IS NOT NULL THEN 1 ELSE 0 END) AS non_blank_rows,
              COUNT(DISTINCT CASE WHEN TRIM(FEIDE) IS NOT NULL THEN TRIM(FEIDE) END) AS distinct_non_blank
            FROM USD_METADATA.BRUKARAR
            WHERE 1=1
              {name_filter_sql}
            """,
            params,
        )

        (duplicate_value_count,) = _one_row(
            connection,
            f"""
            SELECT COUNT(*)
            FROM (
              SELECT TRIM(FEIDE) AS v, COUNT(*) AS c
              FROM USD_METADATA.BRUKARAR
              WHERE TRIM(FEIDE) IS NOT NULL
                {name_filter_sql}
              GROUP BY TRIM(FEIDE)
              HAVING COUNT(*) > 1
            )
            """,
            params,
        )

        (has_at, email_like, numeric_only, contains_space) = _one_row(
            connection,
            f"""
            SELECT
              SUM(CASE WHEN TRIM(FEIDE) IS NOT NULL AND INSTR(TRIM(FEIDE), '@') > 0 THEN 1 ELSE 0 END) AS has_at,
              SUM(CASE WHEN TRIM(FEIDE) IS NOT NULL AND REGEXP_LIKE(TRIM(FEIDE), '^[^@[:space:]]+@[^@[:space:]]+$') THEN 1 ELSE 0 END) AS email_like,
              SUM(CASE WHEN TRIM(FEIDE) IS NOT NULL AND REGEXP_LIKE(TRIM(FEIDE), '^[0-9]+$') THEN 1 ELSE 0 END) AS numeric_only,
              SUM(CASE WHEN TRIM(FEIDE) IS NOT NULL AND REGEXP_LIKE(TRIM(FEIDE), '[[:space:]]') THEN 1 ELSE 0 END) AS contains_space
            FROM USD_METADATA.BRUKARAR
            WHERE 1=1
              {name_filter_sql}
            """,
            params,
        )

        top_domains = _many_rows(
            connection,
            f"""
            SELECT domain, cnt
            FROM (
              SELECT
                LOWER(REGEXP_SUBSTR(TRIM(FEIDE), '@(.+)$', 1, 1, NULL, 1)) AS domain,
                COUNT(*) AS cnt
              FROM USD_METADATA.BRUKARAR
              WHERE TRIM(FEIDE) IS NOT NULL
                {name_filter_sql}
                AND REGEXP_LIKE(TRIM(FEIDE), '^[^@[:space:]]+@[^@[:space:]]+$')
              GROUP BY LOWER(REGEXP_SUBSTR(TRIM(FEIDE), '@(.+)$', 1, 1, NULL, 1))
              ORDER BY cnt DESC
            )
            WHERE ROWNUM <= 25
            """,
            params,
        )

        feide_users_non_blank = _query_dict_rows(
            connection,
            f"""
            SELECT *
            FROM USD_METADATA.BRUKARAR
            WHERE TRIM(FEIDE) IS NOT NULL
              {name_filter_sql}
            ORDER BY USR
            """,
            params,
        )
    finally:
        connection.close()

    generated_at_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    profile = {
        "generated_at_utc": generated_at_utc,
        "source": {
            "env_prefix": config.env_prefix,
            "host": config.host,
            "port": config.port,
            "service_name": config.service_name,
            "table": "USD_METADATA.BRUKARAR",
            "column": "FEIDE",
            "name_contains": name_contains if use_name_filter else None,
        },
        "counts": {
            "total_rows": int(total_rows or 0),
            "null_rows": int(null_rows or 0),
            "blank_or_null_rows": int(blank_or_null_rows or 0),
            "non_blank_rows": int(non_blank_rows or 0),
            "distinct_non_blank": int(distinct_non_blank or 0),
            "duplicate_value_count": int(duplicate_value_count or 0),
        },
        "format_heuristics": {
            "has_at": int(has_at or 0),
            "email_like": int(email_like or 0),
            "numeric_only": int(numeric_only or 0),
            "contains_space": int(contains_space or 0),
        },
        "top_email_domains": [
            {"domain": str(domain), "count": int(count)}
            for domain, count in top_domains
            if domain is not None
        ],
        "row_exports": {
            "non_blank_user_rows": len(feide_users_non_blank),
            "sample_rows": min(10, len(feide_users_non_blank)),
        },
    }

    with tempfile.TemporaryDirectory(prefix="oracle-feide-profile-") as temp_dir:
        out_dir = Path(temp_dir)
        json_path = out_dir / "feide_profile.json"
        md_path = out_dir / "feide_profile.md"
        domains_csv = out_dir / "feide_top_domains.csv"
        users_csv = out_dir / "feide_users_non_blank.csv"
        users_sample_csv = out_dir / "feide_users_non_blank_sample10.csv"
        users_sample_json = out_dir / "feide_users_non_blank_sample10.json"

        json_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        md_path.write_text(_build_markdown_report(profile), encoding="utf-8")
        with domains_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["domain", "count"])
            writer.writeheader()
            writer.writerows(profile["top_email_domains"])
        _write_csv_rows(users_csv, feide_users_non_blank)
        sample_rows = feide_users_non_blank[:10]
        _write_csv_rows(users_sample_csv, sample_rows)
        users_sample_json.write_text(json.dumps(sample_rows, indent=2), encoding="utf-8")

        bucket = os.getenv("S3_BUCKET")
        if not bucket:
            raise ValueError("Missing required S3_BUCKET environment variable")
        prefix = os.getenv("S3_PREFIX", "oracle-schema").strip("/")
        base_prefix = (
            f"{prefix}/feide-field-profile/{generated_at_utc}"
            if prefix
            else f"feide-field-profile/{generated_at_utc}"
        )

        upload_targets = {
            "feide_profile.json": json_path,
            "feide_profile.md": md_path,
            "feide_top_domains.csv": domains_csv,
            "feide_users_non_blank.csv": users_csv,
            "feide_users_non_blank_sample10.csv": users_sample_csv,
            "feide_users_non_blank_sample10.json": users_sample_json,
        }
        uploaded = []
        for filename, local_path in upload_targets.items():
            object_key = f"{base_prefix}/{filename}"
            upload_file_with_compat_retry(str(local_path), bucket, object_key)
            uploaded.append(f"s3://{bucket}/{object_key}")

    logger.info("Uploaded FEIDE profile artifacts:")
    for uri in uploaded:
        logger.info(uri)

    return {"uploaded": uploaded, "summary": profile["counts"]}
