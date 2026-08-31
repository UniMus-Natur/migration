"""Unit tests for MUSIT typification → CollectionObject field mapping."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from flows.lib.musit_typification import (
    build_typification_co_field_updates,
    literature_tab_text_from_bundle,
    merge_typification_notes,
    parse_typification_year,
)


class MusitTypificationTests(unittest.TestCase):
    def test_parse_typification_year_from_work_date(self) -> None:
        self.assertEqual(
            parse_typification_year([{"work_date": "1952", "from_date": None}]),
            1952,
        )

    def test_merge_typification_notes_deduplicates(self) -> None:
        self.assertEqual(
            merge_typification_notes(
                [{"note": "Same note"}, {"note": "Same note"}, {"note": "Other"}]
            ),
            "Same note; Other",
        )

    def test_literature_tab_text_from_bundle(self) -> None:
        specimen, taxon = literature_tab_text_from_bundle(
            {
                "specimen": [{"reference": "Flora 1952", "title": None}],
                "taxon": [{"reference": "Nordic J Bot", "title": None}],
            }
        )
        self.assertEqual(specimen, "Flora 1952")
        self.assertEqual(taxon, "Nordic J Bot")

    def test_build_typification_co_field_updates(self) -> None:
        agent1 = MagicMock()
        agent2 = MagicMock()

        def resolve_agent(_schema: str, actor_id: int, agent_cache=None):
            return {101: agent1, 102: agent2}.get(int(actor_id))

        updates = build_typification_co_field_updates(
            type_status_raw="ex-holotype",
            type_info=[
                {
                    "work_date": "1998",
                    "note": "Designated here.",
                    "document_id": None,
                }
            ],
            typification_meta={
                "designator_actor_ids": [101, 102],
                "specimen_literature": "Specimen ref",
                "taxon_literature": "Taxon ref",
            },
            literature_bundle={"specimen": [], "taxon": [], "type_info": []},
            schema="MUSIT_BOTANIKK_FELLES",
            agent_cache={101: 1, 102: 2},
            resolve_agent=resolve_agent,
        )
        self.assertEqual(updates["restrictions"], "Exholotype")
        self.assertEqual(updates["integer2"], 1998)
        self.assertEqual(updates["reservedtext3"], "Designated here.")
        self.assertIs(updates["agent1"], agent1)
        self.assertIs(updates["cataloger"], agent2)
        self.assertEqual(updates["ocr"], "Specimen ref")
        self.assertEqual(updates["embargoreason"], "Taxon ref")


if __name__ == "__main__":
    unittest.main()
