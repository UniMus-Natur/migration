"""Tests for MUSIT admin geography source (MV_HIERARKISK_STED)."""

from __future__ import annotations

import unittest

from flows.lib.oracle_geography_admin import (
    fetch_hierarchical_chain_rows_for_place,
    hierarchical_admin_relation,
    oracle_type_name_to_rank_item_name,
)


class OracleGeographyAdminSourceTests(unittest.TestCase):
    def test_hierarchical_admin_relation(self) -> None:
        self.assertEqual(
            hierarchical_admin_relation("MUSIT_BOTANIKK_FELLES"),
            "MUSIT_BOTANIKK_FELLES.MV_HIERARKISK_STED",
        )

    def test_gammelt_fylke_maps_to_county(self) -> None:
        self.assertEqual(oracle_type_name_to_rank_item_name("Gammelt fylke"), "County")

    def test_current_fylke_maps_to_county(self) -> None:
        self.assertEqual(oracle_type_name_to_rank_item_name("Fylke"), "County")

    def test_fetch_chain_orders_parents_before_children(self) -> None:
        rows_by_hid = {
            5099: {"hid": 5099, "name": "Sem", "partof": 4519, "type_name": "Gammel kommune"},
            4519: {"hid": 4519, "name": "Tønsberg", "partof": 6449, "type_name": "Gammel kommune"},
            6449: {"hid": 6449, "name": "Tønsberg", "partof": 6400, "type_name": "Kommune"},
            6400: {
                "hid": 6400,
                "name": "Vestfold og Telemark",
                "partof": 2486,
                "type_name": "Fylke",
            },
            2486: {"hid": 2486, "name": "Norway", "partof": 1935, "type_name": "Land"},
        }

        class FakeCursor:
            def __init__(self) -> None:
                self.place_id: int | None = None
                self.hid: int | None = None

            def execute(self, sql: str, params: dict | None = None) -> None:
                if "place_hierachical_place" in sql:
                    self.place_id = int(params["pid"])  # type: ignore[index]
                elif "HIERARCH_PLACE_ID = :hid" in sql:
                    self.hid = int(params["hid"])  # type: ignore[index]

            def fetchall(self) -> list[tuple[int]]:
                if self.place_id == 2440609:
                    return [(5099,)]
                return []

            def fetchone(self) -> tuple | None:
                row = rows_by_hid.get(int(self.hid or 0))
                if row is None:
                    return None
                return (row["hid"], row["name"], row["partof"], row["type_name"])

        ordered = fetch_hierarchical_chain_rows_for_place(FakeCursor(), "MUSIT_BOTANIKK_FELLES", 2440609)
        names = [r["name"] for r in ordered]
        self.assertEqual(names[0], "Norway")
        self.assertIn("Vestfold og Telemark", names)
        self.assertEqual(names[-1], "Sem")


if __name__ == "__main__":
    unittest.main()
