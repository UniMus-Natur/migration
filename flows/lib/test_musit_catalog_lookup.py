"""Unit tests for MUSIT catalog → object_id resolution helpers."""

from __future__ import annotations

import unittest

from flows.lib.musit_catalog_lookup import _resolve_catalog_to_object_ids


class _FakeCursor:
    def __init__(self, responses: list[list[tuple]]) -> None:
        self._responses = list(responses)
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql: str, binds: dict | None = None) -> None:
        self.executed.append((sql, binds or {}))

    def fetchall(self) -> list[tuple]:
        if not self._responses:
            return []
        return self._responses.pop(0)


class MusitCatalogLookupTests(unittest.TestCase):
    def test_exact_identifier_string_match(self) -> None:
        cursor = _FakeCursor([[(12345,)]])
        self.assertEqual(
            _resolve_catalog_to_object_ids(cursor, "MUSIT_BOTANIKK_FELLES", "O-V-398038"),
            [12345],
        )
        self.assertEqual(len(cursor.executed), 1)

    def test_falls_back_to_identifier_num(self) -> None:
        cursor = _FakeCursor([[], [], [], [(99,)]])
        self.assertEqual(
            _resolve_catalog_to_object_ids(cursor, "MUSIT_BOTANIKK_FELLES", "398038"),
            [99],
        )
        self.assertEqual(len(cursor.executed), 4)


if __name__ == "__main__":
    unittest.main()
