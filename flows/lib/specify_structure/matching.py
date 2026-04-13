"""Pure matching helpers (unit-tested without Django)."""

from __future__ import annotations


def norm_name(value: str) -> str:
    return (value or "").strip()


def norm_code(value: str) -> str:
    return (value or "").strip()


def division_key(institution_id: int, division_name: str) -> tuple[int, str]:
    return (institution_id, norm_name(division_name).casefold())


def discipline_name_key(discipline_name: str) -> str:
    """Specify enforces discipline name uniqueness globally (case-insensitive)."""
    return norm_name(discipline_name).casefold()


def collection_key(discipline_id: int, code: str) -> tuple[int, str]:
    return (discipline_id, norm_code(code).casefold())
