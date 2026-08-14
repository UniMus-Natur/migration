"""Unit tests for MUSIT classification-event determiner actor aggregation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from flows.lib.musit_determiner_actors import (
    classification_determiner_actor_ids_for_det_key,
    classification_event_ids_for_det_key,
    determination_dedupe_key,
    fetch_event_role_actor_ids,
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
        # Join fan-out on one event still shares one key.
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

    def test_fetch_event_role_actor_ids_dedupes_across_sources(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.side_effect = [((11,), (12,)), ((12,), (13,))]
        self.assertEqual(
            fetch_event_role_actor_ids(cursor, "MUSIT_BOTANIKK_FELLES", 999),
            [11, 12, 13],
        )
        self.assertEqual(cursor.execute.call_count, 2)

    def test_classification_determiner_actor_ids_stay_on_one_event(self) -> None:
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
        cursor.fetchall.side_effect = [
            ((10,),),
            (),
        ]
        det_key = determination_dedupe_key(rows[0])
        self.assertEqual(
            classification_determiner_actor_ids_for_det_key(
                rows, det_key, cursor, "MUSIT_BOTANIKK_FELLES"
            ),
            [10],
        )


if __name__ == "__main__":
    unittest.main()
