"""Unit tests for MUSIT literature archive + type-publication mapping helpers."""

from __future__ import annotations

import unittest

from flows.lib.musit_literature import (
    extract_work_date,
    literature_archive_payload,
    musit_document_guid,
    reference_work_title,
    taxon_literature_for_event_ids,
)


class MusitLiteratureTests(unittest.TestCase):
    def test_work_date_from_time_as_text_year(self) -> None:
        self.assertEqual(
            extract_work_date(from_date="2026-05-04T00:00:00", time_as_text="Year Type info! 2025"),
            "2025",
        )

    def test_work_date_from_from_date_when_no_year_text(self) -> None:
        self.assertEqual(extract_work_date(from_date="1998-03-12", time_as_text=None), "1998")

    def test_work_date_none_when_empty(self) -> None:
        self.assertIsNone(extract_work_date())

    def test_archive_payload_omits_empty_sections(self) -> None:
        self.assertIsNone(literature_archive_payload({"specimen": [], "taxon": [], "type_info": []}))
        payload = literature_archive_payload(
            {
                "specimen": [{"document_id": 1, "reference": "Lids flora 1952", "title": None}],
                "taxon": [],
                "type_info": [],
            }
        )
        self.assertEqual(list(payload), ["specimen_literature"])
        self.assertEqual(payload["specimen_literature"][0]["document_id"], 1)

    def test_taxon_literature_filtered_by_event(self) -> None:
        rows = [
            {"document_id": 10, "event_id": 100, "reference": "a"},
            {"document_id": 11, "event_id": 200, "reference": "b"},
        ]
        self.assertEqual(
            taxon_literature_for_event_ids(rows, [200]),
            [{"document_id": 11, "event_id": 200, "reference": "b"}],
        )

    def test_document_guid_and_title(self) -> None:
        self.assertEqual(musit_document_guid(443611), "urn:oracle:musit:document:443611")
        self.assertEqual(
            reference_work_title({"reference": "Nordic Journal of Botany", "title": None}),
            "Nordic Journal of Botany",
        )
        self.assertIsNone(reference_work_title({"reference": None, "title": None}))


if __name__ == "__main__":
    unittest.main()
