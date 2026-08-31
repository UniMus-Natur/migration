"""Unit tests for MUSIT classification-event determiner actor aggregation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from flows.lib.musit_determiner_actors import (
    EventPersonRole,
    classification_determiner_roles_for_det_key,
    classification_event_ids_for_det_key,
    determination_dedupe_key,
    fetch_event_person_roles,
)


class MusitDeterminerActorIdsTests(unittest.TestCase):
    def test_dedupe_key_is_per_classification_event(self) -> None:
        same_taxon = {
            "adb_taxon_id": 1,
            "adb_latin_name_id": None,
            "latin_name_id": 10,
            "valid_classterm": "Carex digitata L.",
            "classterm": "Carex digitata L.",
        }
        e2015 = {**same_taxon, "class_event_id": 100, "class_from_date": "2015-03-13"}
        e2019 = {**same_taxon, "class_event_id": 200, "class_from_date": "2019-03-14"}
        self.assertNotEqual(determination_dedupe_key(e2015), determination_dedupe_key(e2019))
        e2019_dup = {**e2019, "latin_name_id": 99}
        self.assertEqual(determination_dedupe_key(e2019), determination_dedupe_key(e2019_dup))

    def test_classification_event_ids_for_det_key(self) -> None:
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
        det_key = determination_dedupe_key(rows[0])
        self.assertEqual(classification_event_ids_for_det_key(rows, det_key), [100])
        self.assertEqual(
            classification_event_ids_for_det_key(rows, determination_dedupe_key(rows[2])),
            [200],
        )

    def test_fetch_event_person_roles_returns_actor_ids(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [(11, 1, "DET"), (12, 2, "DETSCR")]
        roles = fetch_event_person_roles(
            cursor,
            "MUSIT_BOTANIKK_FELLES",
            999,
            role_terms=frozenset({"DET", "DETSCR"}),
        )
        self.assertEqual([r.actor_id for r in roles], [11, 12])
        self.assertFalse(roles[0].is_scr)
        self.assertTrue(roles[1].is_scr)
        self.assertEqual(cursor.execute.call_count, 1)

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


if __name__ == "__main__":
    unittest.main()
