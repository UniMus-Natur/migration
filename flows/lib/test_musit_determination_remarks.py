"""Unit tests for MUSIT classification-event note → Determination.remarks mapping."""

from __future__ import annotations

import unittest

from flows.lib.musit_determination_remarks import determination_remarks


class MusitDeterminationRemarksTests(unittest.TestCase):
    def test_event_notes_only(self) -> None:
        dr = {"event_notes": "nom cons."}
        self.assertEqual(
            determination_remarks(dr, has_resolved_determiner=True), "nom cons."
        )

    def test_verbatim_determiner_when_no_agent(self) -> None:
        dr = {"det_agg_personnames": "Smith, J."}
        self.assertEqual(
            determination_remarks(dr, has_resolved_determiner=False),
            "Determiner (verbatim): Smith, J.",
        )

    def test_event_notes_and_verbatim_determiner(self) -> None:
        dr = {"event_notes": "nom cons.", "detname_orig": "Smith, J."}
        self.assertEqual(
            determination_remarks(dr, has_resolved_determiner=False),
            "nom cons.; Determiner (verbatim): Smith, J.",
        )

    def test_event_notes_without_verbatim_when_agent_resolved(self) -> None:
        dr = {"event_notes": "nom cons.", "det_agg_personnames": "Smith, J."}
        self.assertEqual(
            determination_remarks(dr, has_resolved_determiner=True), "nom cons."
        )

    def test_partial_unresolved_determiner_names(self) -> None:
        dr = {"event_notes": "nom cons."}
        self.assertEqual(
            determination_remarks(
                dr,
                has_resolved_determiner=True,
                unresolved_determiner_names=["Elven, Reidar"],
            ),
            "nom cons.; Determiner (unresolved): Elven, Reidar",
        )

    def test_empty_when_no_notes_or_verbatim(self) -> None:
        self.assertIsNone(determination_remarks({}, has_resolved_determiner=True))


if __name__ == "__main__":
    unittest.main()
