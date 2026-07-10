"""Unit tests for MUSIT classification-event note → Determination.remarks mapping."""

from __future__ import annotations

import unittest

from flows.lib.musit_determination_remarks import determination_remarks


class MusitDeterminationRemarksTests(unittest.TestCase):
    def test_event_notes_only(self) -> None:
        dr = {"event_notes": "nom cons."}
        self.assertEqual(determination_remarks(dr, determiner=object()), "nom cons.")

    def test_verbatim_determiner_when_no_agent(self) -> None:
        dr = {"det_agg_personnames": "Smith, J."}
        self.assertEqual(
            determination_remarks(dr, determiner=None),
            "Determiner (verbatim): Smith, J.",
        )

    def test_event_notes_and_verbatim_determiner(self) -> None:
        dr = {"event_notes": "nom cons.", "detname_orig": "Smith, J."}
        self.assertEqual(
            determination_remarks(dr, determiner=None),
            "nom cons.; Determiner (verbatim): Smith, J.",
        )

    def test_event_notes_without_verbatim_when_agent_resolved(self) -> None:
        dr = {"event_notes": "nom cons.", "det_agg_personnames": "Smith, J."}
        self.assertEqual(determination_remarks(dr, determiner=object()), "nom cons.")

    def test_empty_when_no_notes_or_verbatim(self) -> None:
        self.assertIsNone(determination_remarks({}, determiner=object()))


if __name__ == "__main__":
    unittest.main()
