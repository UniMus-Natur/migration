"""Split one MUSIT collection into Specify collections by catalog number."""

from __future__ import annotations


def identifier_num_filter_sql(
    *,
    oracle_schema: str,
    identifier_num_at_least: int | None,
    identifier_num_below: int | None,
) -> tuple[str, str, dict[str, int]]:
    """Return ``(join_sql, where_sql, binds)`` for an optional catalog-number series.

    ``where_sql`` is empty or starts with ``AND``. No filter when both bounds are unset.
    A ``below`` bound keeps rows whose ``IDENTIFIER_NUM`` is NULL.
    """
    if identifier_num_at_least is None and identifier_num_below is None:
        return "", "", {}
    join_sql = (
        f"JOIN {oracle_schema}.museum_object mo ON mo.object_id = voa.object_id"
    )
    wheres: list[str] = []
    binds: dict[str, int] = {}
    if identifier_num_at_least is not None:
        wheres.append("mo.identifier_num >= :id_at_least")
        binds["id_at_least"] = int(identifier_num_at_least)
    if identifier_num_below is not None:
        wheres.append("(mo.identifier_num IS NULL OR mo.identifier_num < :id_below)")
        binds["id_below"] = int(identifier_num_below)
    return join_sql, " AND " + " AND ".join(wheres), binds
