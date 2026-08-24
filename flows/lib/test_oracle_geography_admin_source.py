"""Tests for MUSIT admin geography source (MV_HIERARKISK_STED)."""

from __future__ import annotations

import unittest

from flows.lib.oracle_geography_admin import (
    NORWEGIAN_GEOGRAPHY_RANKS,
    fetch_hierarchical_chain_rows_for_place,
    hierarchical_admin_relation,
    oracle_row_is_world_or_planet_shell,
    oracle_type_name_to_rank_item_name,
    should_alias_geography_to_parent,
)


def _assign_norwegian_ranks(chain: list[tuple[str, str]]) -> list[str | None]:
    """Walk a MUSIT chain and return the Specify rank name used for each row (None = aliased)."""
    rankid_by_name = {spec.name: spec.rankid for spec in NORWEGIAN_GEOGRAPHY_RANKS}
    parent_name = "Earth"
    parent_rankid = 0
    parent_is_earth = True
    assigned: list[str | None] = []
    for name, type_name in chain:
        if oracle_row_is_world_or_planet_shell(name, type_name):
            assigned.append("Earth")
            continue
        if should_alias_geography_to_parent(
            child_name=name,
            parent_name=parent_name,
            parent_is_earth=parent_is_earth,
        ):
            assigned.append(None)
            continue
        logical = oracle_type_name_to_rank_item_name(type_name)
        rankid = rankid_by_name.get(logical)
        if rankid is None or rankid <= parent_rankid:
            spec = next(s for s in NORWEGIAN_GEOGRAPHY_RANKS if s.rankid > parent_rankid)
            logical = spec.name
            rankid = spec.rankid
        assigned.append(logical)
        parent_name = name
        parent_rankid = rankid
        parent_is_earth = False
    return assigned


class OracleGeographyAdminSourceTests(unittest.TestCase):
    def test_hierarchical_admin_relation(self) -> None:
        self.assertEqual(
            hierarchical_admin_relation("MUSIT_BOTANIKK_FELLES"),
            "MUSIT_BOTANIKK_FELLES.MV_HIERARKISK_STED",
        )

    def test_type_names_map_to_norwegian_ranks(self) -> None:
        self.assertEqual(oracle_type_name_to_rank_item_name("Planet"), "Earth")
        self.assertEqual(oracle_type_name_to_rank_item_name("Continent"), "Continent")
        self.assertEqual(oracle_type_name_to_rank_item_name("Ocean"), "Ocean")
        self.assertEqual(oracle_type_name_to_rank_item_name("Sea"), "Sea")
        self.assertEqual(oracle_type_name_to_rank_item_name("Land"), "Land")
        self.assertEqual(oracle_type_name_to_rank_item_name("Fylke"), "Fylke")
        self.assertEqual(oracle_type_name_to_rank_item_name("Gammelt fylke"), "Gammelt fylke")
        self.assertEqual(oracle_type_name_to_rank_item_name("Region"), "Region")
        self.assertEqual(oracle_type_name_to_rank_item_name("Sub region"), "Sub region")
        self.assertEqual(oracle_type_name_to_rank_item_name("Kommune"), "Kommune")
        self.assertEqual(oracle_type_name_to_rank_item_name("Gammel kommune"), "Gammel kommune")
        self.assertEqual(oracle_type_name_to_rank_item_name(""), "")
        self.assertEqual(oracle_type_name_to_rank_item_name(None), "")

    def test_gammel_kommune_is_not_mapped_as_kommune(self) -> None:
        self.assertNotEqual(oracle_type_name_to_rank_item_name("Gammel kommune"), "Kommune")
        self.assertNotEqual(oracle_type_name_to_rank_item_name("Gammelt fylke"), "Fylke")
        self.assertNotEqual(oracle_type_name_to_rank_item_name("Sub region"), "Region")

    def test_same_name_parent_is_aliased_except_earth(self) -> None:
        self.assertTrue(
            should_alias_geography_to_parent(
                child_name="Tønsberg",
                parent_name="Tønsberg",
                parent_is_earth=False,
            )
        )
        self.assertFalse(
            should_alias_geography_to_parent(
                child_name="Sem",
                parent_name="Tønsberg",
                parent_is_earth=False,
            )
        )
        self.assertFalse(
            should_alias_geography_to_parent(
                child_name="WORLD",
                parent_name="Earth",
                parent_is_earth=True,
            )
        )

    def test_tonsberg_sem_chain_uses_gammel_kommune_for_sem(self) -> None:
        ranks = _assign_norwegian_ranks(
            [
                ("WORLD", "Planet"),
                ("EUROPE", "Continent"),
                ("Norway", "Land"),
                ("Vestfold og Telemark", "Fylke"),
                ("Tønsberg", "Kommune"),
                ("Tønsberg", "Gammel kommune"),
                ("Sem", "Gammel kommune"),
            ]
        )
        self.assertEqual(
            ranks,
            ["Earth", "Continent", "Land", "Fylke", "Kommune", None, "Gammel kommune"],
        )

    def test_nested_gammel_kommune_with_different_names_uses_sted(self) -> None:
        ranks = _assign_norwegian_ranks(
            [
                ("Norway", "Land"),
                ("Vestfold og Telemark", "Fylke"),
                ("Holmestrand", "Kommune"),
                ("Botne", "Gammel kommune"),
                ("Hillestad", "Gammel kommune"),
            ]
        )
        self.assertEqual(ranks, ["Land", "Fylke", "Kommune", "Gammel kommune", "Sted"])

    def test_kommune_under_region_skips_fylke(self) -> None:
        ranks = _assign_norwegian_ranks(
            [
                ("Norway", "Land"),
                ("Svalbard", "Region"),
                ("Longyearbyen", "Kommune"),
            ]
        )
        self.assertEqual(ranks, ["Land", "Region", "Kommune"])

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
