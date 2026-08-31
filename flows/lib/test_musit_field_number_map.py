"""Unit tests for MUSIT field number / object number mapping."""

from __future__ import annotations

import unittest

from flows.lib.musit_field_number_map import (
    coerce_musit_identifier_num,
    legnr_by_actor,
    resolve_field_number_from_legnr_rows,
)


class MusitFieldNumberMapTests(unittest.TestCase):
    def test_coerce_identifier_num(self) -> None:
        self.assertEqual(coerce_musit_identifier_num(2000001), 2000001)
        self.assertEqual(coerce_musit_identifier_num(2000001.0), 2000001)
        self.assertIsNone(coerce_musit_identifier_num(None))
        self.assertIsNone(coerce_musit_identifier_num("abc"))

    def test_primary_collector_legnr_preferred(self) -> None:
        rows = [
            {"actor_id": 100, "legnr": "11550"},
            {"actor_id": 200, "legnr": "10883"},
        ]
        value, meta = resolve_field_number_from_legnr_rows(rows, primary_actor_id=200)
        self.assertEqual(value, "10883")
        self.assertEqual(meta["source"], "primary_collector")

    def test_single_row_without_primary(self) -> None:
        rows = [{"actor_id": 36228, "legnr": "852A"}]
        value, meta = resolve_field_number_from_legnr_rows(rows, primary_actor_id=None)
        self.assertEqual(value, "852A")
        self.assertEqual(meta["source"], "single_legnr_row")

    def test_multi_collector_join_when_no_primary_match(self) -> None:
        rows = [
            {"actor_id": 100, "legnr": "11550"},
            {"actor_id": 200, "legnr": "10883"},
        ]
        value, meta = resolve_field_number_from_legnr_rows(rows, primary_actor_id=999)
        self.assertEqual(value, "11550; 10883")
        self.assertEqual(meta["source"], "multi_collector_join")

    def test_legnr_by_actor(self) -> None:
        rows = [
            {"actor_id": 39829, "legnr": "134"},
            {"actor_id": 39829, "legnr": "134"},
        ]
        self.assertEqual(legnr_by_actor(rows), {39829: "134"})

    def test_dedupes_duplicate_actor_legnr_pairs(self) -> None:
        rows = [
            {"actor_id": 100, "legnr": "1989"},
            {"actor_id": 100, "legnr": "1989"},
        ]
        value, meta = resolve_field_number_from_legnr_rows(rows, primary_actor_id=100)
        self.assertEqual(value, "1989")
        self.assertEqual(meta["source"], "primary_collector")


if __name__ == "__main__":
    unittest.main()
