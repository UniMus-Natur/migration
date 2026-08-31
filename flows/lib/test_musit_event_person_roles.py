"""Unit tests for MUSIT event person roles (Leg/Det/Scr)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from flows.lib.musit_determiner_actors import (
    EventPersonRole,
    classification_determiner_actor_ids_for_det_key,
    classification_determiner_roles_for_det_key,
    determination_dedupe_key,
    fetch_event_person_roles,
    ordernumbers_for_roles,
)


class MusitEventPersonRolesTests(unittest.TestCase):
    def test_ordernumbers_use_sorting_sequence(self) -> None:
        roles = [
            EventPersonRole(actor_id=1, sorting_sequence=1, is_scr=False),
            EventPersonRole(actor_id=2, sorting_sequence=2, is_scr=True),
        ]
        self.assertEqual(ordernumbers_for_roles(roles), [1, 2])

    def test_ordernumbers_append_null_sorts_after_max(self) -> None:
        roles = [
            EventPersonRole(actor_id=1, sorting_sequence=1, is_scr=False),
            EventPersonRole(actor_id=2, sorting_sequence=None, is_scr=False),
        ]
        self.assertEqual(ordernumbers_for_roles(roles), [1, 2])

    def test_ordernumbers_all_null_start_at_one(self) -> None:
        roles = [EventPersonRole(actor_id=9, sorting_sequence=None, is_scr=False)]
        self.assertEqual(ordernumbers_for_roles(roles), [1])

    def test_fetch_event_person_roles_filters_and_marks_scr(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (39829, 1, "LEGSCR"),
            (33926, 2, "LEG"),
        ]
        roles = fetch_event_person_roles(
            cursor,
            "MUSIT_BOTANIKK_FELLES",
            9949682,
            role_terms=frozenset({"LEG", "LEGSCR"}),
        )
        self.assertEqual(len(roles), 2)
        self.assertTrue(roles[0].is_scr)
        self.assertFalse(roles[1].is_scr)

    def test_fetch_event_person_roles_dedupes_actor_id(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (10, 1, "DET"),
            (10, 2, "DETSCR"),
        ]
        roles = fetch_event_person_roles(
            cursor,
            "MUSIT_BOTANIKK_FELLES",
            100,
            role_terms=frozenset({"DET", "DETSCR"}),
        )
        self.assertEqual([r.actor_id for r in roles], [10])
        self.assertFalse(roles[0].is_scr)

    def test_classification_determiner_roles_stay_on_one_event(self) -> None:
        rows = [
            {
                "class_event_id": 100,
                "adb_taxon_id": 1,
                "adb_latin_name_id": None,
                "latin_name_id": None,
                "valid_classterm": None,
                "classterm": None,
            },
            {
                "class_event_id": 200,
                "adb_taxon_id": 1,
                "adb_latin_name_id": None,
                "latin_name_id": None,
                "valid_classterm": None,
                "classterm": None,
            },
        ]
        cursor = MagicMock()
        cursor.fetchall.return_value = [(10, 1, "DET")]
        det_key = determination_dedupe_key(rows[0])
        self.assertEqual(
            classification_determiner_roles_for_det_key(
                rows, det_key, cursor, "MUSIT_BOTANIKK_FELLES"
            ),
            [EventPersonRole(actor_id=10, sorting_sequence=1, is_scr=False)],
        )
        self.assertEqual(
            classification_determiner_actor_ids_for_det_key(
                rows, det_key, cursor, "MUSIT_BOTANIKK_FELLES"
            ),
            [10],
        )


if __name__ == "__main__":
    unittest.main()
