"""Map MUSIT CLASSIFICATION_TERM.SENSU_TERM to Specify Determination.addendum."""

from __future__ import annotations

from typing import Any

# Values observed in MUSIT_BOTANIKK_FELLES PROD (see scripts/analyze_sensu_term.py).
EXPECTED_SENSU_ADDENDUM = frozenset({"s.lat.", "s.str."})


def resolve_sensu_addendum(raw: Any) -> tuple[str | None, str | None, bool]:
    """Return ``(addendum, archived_value, is_outlier)`` for one SENSU_TERM.

    Standard picklist values map to ``Determination.addendum``.  Outliers (mis-keyed
    epithet/author fragments) are preserved in the CollectionObject JSON catchall instead.
    """
    if raw is None:
        return None, None, False
    value = str(raw).strip()
    if not value:
        return None, None, False
    if value in EXPECTED_SENSU_ADDENDUM:
        return value, value, False
    return None, value, True


def classification_sensu_outliers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect non-standard SENSU_TERM rows for ``CollectionObject.text3`` archival."""
    outliers: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if row.get("class_event_id") is None:
            continue
        _addendum, archived, is_outlier = resolve_sensu_addendum(row.get("sensu_term"))
        if not is_outlier or archived is None:
            continue
        key = (row.get("class_event_id"), row.get("class_term_id"), archived)
        if key in seen:
            continue
        seen.add(key)
        outliers.append(
            {
                "class_event_id": row.get("class_event_id"),
                "class_term_id": row.get("class_term_id"),
                "sensu_term": archived,
                "classterm": row.get("classterm") or row.get("valid_classterm"),
            }
        )
    return outliers
