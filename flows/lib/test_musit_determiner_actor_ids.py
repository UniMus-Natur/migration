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
            {
                "class_event_id": 300,
                "adb_taxon_id": 2,
                "adb_latin_name_id": None,
                "latin_name_id": None,
                "valid_classterm": None,
                "classterm": None,
            },
        ]
        det_key = determination_dedupe_key(rows[0])
        self.assertEqual(classification_event_ids_for_det_key(rows, det_key), [100, 200])

    def test_fetch_event_role_actor_ids_dedupes_across_sources(self) -> None:
        cursor = MagicMock()
        cursor.fetchall.side_effect = [((11,), (12,)), ((12,), (13,))]
        self.assertEqual(
            fetch_event_role_actor_ids(cursor, "MUSIT_BOTANIKK_FELLES", 999),
            [11, 12, 13],
        )
        self.assertEqual(cursor.execute.call_count, 2)

    def test_classification_determiner_actor_ids_merges_events(self) -> None:
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
            ((20,), (21,)),
            (),
        ]
        det_key = determination_dedupe_key(rows[0])
        self.assertEqual(
            classification_determiner_actor_ids_for_det_key(
                rows, det_key, cursor, "MUSIT_BOTANIKK_FELLES"
            ),
            [10, 20, 21],
        )


if __name__ == "__main__":
    unittest.main()
