"""Unit tests for the single-pass MUSIT agent bundle."""

from __future__ import annotations

import unittest

from flows.lib.musit_agent_bundle import (
    PersonNameRow,
    alternate_variant_names,
    bundle_from_joined_rows,
    pick_preferred_person_name,
)


class BundleTests(unittest.TestCase):
    def test_groups_person_names_and_keeps_actor_without_names(self) -> None:
        bundle = bundle_from_joined_rows(
            [
                {
                    "actor_id": 1,
                    "actor_type": 0,
                    "actorname": "Blytt, Mathias N.",
                    "person_name_id": 10,
                    "person_surname": "Bl.",
                    "person_given_name": "M.",
                    "person_middle_name": "N.",
                    "title": None,
                },
                {
                    "actor_id": 1,
                    "actor_type": 0,
                    "actorname": "Blytt, Mathias N.",
                    "person_name_id": 11,
                    "person_surname": "Blytt",
                    "person_given_name": "Mathias",
                    "person_middle_name": "Numsen",
                    "title": "Dr.",
                },
            ]
        )
        self.assertEqual(bundle.actor_id, 1)
        self.assertEqual(len(bundle.person_names), 2)

        empty = bundle_from_joined_rows(
            [{"actor_id": 2, "actor_type": 0, "actorname": "No Names", "person_name_id": None}]
        )
        self.assertEqual(empty.person_names, [])

    def test_preferred_matches_actorname_else_fullest(self) -> None:
        short = PersonNameRow(1, "M.", "Bl.", "N.", None)
        full = PersonNameRow(2, "Mathias", "Blytt", "Numsen", "Dr.")
        exact = PersonNameRow(3, "Mathias", "Blytt", "N.", None)
        self.assertEqual(
            pick_preferred_person_name([short, full], "Blytt, Mathias Numsen").person_name_id,
            2,
        )
        self.assertEqual(
            pick_preferred_person_name([full, exact], "Blytt, Mathias N.").person_name_id,
            3,
        )

    def test_variants_keep_spellings_other_than_actorname(self) -> None:
        names = [
            PersonNameRow(1, "M.", "Bl.", "N.", None),
            PersonNameRow(2, "Mathias", "Blytt", "Numsen", None),
        ]
        self.assertEqual(
            alternate_variant_names(names, "Blytt, Mathias Numsen"),
            ["Bl., M. N."],
        )
        self.assertEqual(
            alternate_variant_names(names, "Blytt, Mathias N."),
            ["Bl., M. N.", "Blytt, Mathias Numsen"],
        )


if __name__ == "__main__":
    unittest.main()
