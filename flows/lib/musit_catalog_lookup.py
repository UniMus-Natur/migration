"""Resolve MUSIT catalog numbers / object ids for targeted specimen migration."""

from __future__ import annotations

from typing import Any


def resolve_musit_object_ids(
    oracle_cursor: Any,
    *,
    oracle_schema: str,
    institutioncode: str,
    collectioncode: str,
    catalog_number: str | None = None,
    object_id: int | None = None,
) -> list[int]:
    """Resolve a single catalog number or Oracle ``object_id`` for one dataset filter.

    Returns validated ``object_id`` values that belong to the institution/collection filter.
    Raises ``ValueError`` when both selectors are set, ``RuntimeError`` when none match.
    """
    if catalog_number and object_id is not None:
        raise ValueError("Specify only one of catalog_number or object_id")
    if not catalog_number and object_id is None:
        return []

    schema = str(oracle_schema).strip().upper()

    if object_id is not None:
        candidate_ids = [int(object_id)]
    else:
        candidate_ids = _resolve_catalog_to_object_ids(
            oracle_cursor, schema, catalog_number.strip()
        )
        if not candidate_ids:
            raise RuntimeError(
                f"No MUSIT object found for catalog number {catalog_number!r} "
                f"in schema {schema}"
            )
        if len(candidate_ids) > 1:
            raise RuntimeError(
                f"Catalog number {catalog_number!r} matched multiple MUSIT objects: "
                f"{candidate_ids}"
            )

    validated: list[int] = []
    for oid in candidate_ids:
        oracle_cursor.execute(
            f"""
            SELECT object_id
              FROM {schema}.v_object_attributes
             WHERE object_id = :oid
               AND institutioncode = :icode
               AND collectioncode = :ccode
            """,
            {
                "oid": int(oid),
                "icode": institutioncode,
                "ccode": collectioncode,
            },
        )
        row = oracle_cursor.fetchone()
        if row is None:
            raise RuntimeError(
                f"object_id={oid} is not in {institutioncode}/{collectioncode} "
                f"for schema {schema}"
            )
        validated.append(int(row[0]))
    return validated


def _resolve_catalog_to_object_ids(
    oracle_cursor: Any,
    schema: str,
    catalog: str,
) -> list[int]:
    """Map a catalog string to ``MUSEUM_OBJECT.OBJECT_ID`` values in ``schema``."""
    sch = str(schema).strip().upper()

    queries = [
        (
            f"SELECT object_id FROM {sch}.museum_object WHERE identifier_string = :cat",
            {"cat": catalog},
        ),
        (
            f"SELECT object_id FROM {sch}.museum_object"
            " WHERE UPPER(identifier_string) = UPPER(:cat)",
            {"cat": catalog},
        ),
        (
            f"SELECT object_id FROM {sch}.museum_object WHERE identifier_string LIKE :pat",
            {"pat": f"%{catalog}%"},
        ),
    ]

    for sql, binds in queries:
        oracle_cursor.execute(sql, binds)
        ids = [int(row[0]) for row in oracle_cursor.fetchall()]
        if ids:
            return ids

    try:
        num = int(catalog.replace(" ", ""))
    except ValueError:
        return []

    oracle_cursor.execute(
        f"SELECT object_id FROM {sch}.museum_object WHERE identifier_num = :num",
        {"num": num},
    )
    return [int(row[0]) for row in oracle_cursor.fetchall()]
