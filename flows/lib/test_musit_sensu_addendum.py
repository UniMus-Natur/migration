"""Unit tests for MUSIT SENSU_TERM → Specify addendum mapping."""

from __future__ import annotations

import unittest

from flows.lib.musit_sensu_addendum import (
    classification_sensu_outliers,
    resolve_sensu_addendum,
)


class MusitSensuAddendumTests(unittest.TestCase):
    def test_standard_values_map_to_addendum(self) -> None:
        for value in ("s.lat.", "s.str."):
            addendum, archived, is_outlier = resolve_sensu_addendum(value)
            self.assertEqual(addendum, value)
            self.assertEqual(archived, value)
            self.assertFalse(is_outlier)

    def test_outlier_not_mapped_to_addendum(self) -> None:
        addendum, archived, is_outlier = resolve_sensu_addendum("(Presl)")
        self.assertIsNone(addendum)
        self.assertEqual(archived, "(Presl)")
        self.assertTrue(is_outlier)

    def test_null_and_blank(self) -> None:
        for raw in (None, "", "   "):
            addendum, archived, is_outlier = resolve_sensu_addendum(raw)
            self.assertIsNone(addendum)
            self.assertIsNone(archived)
            self.assertFalse(is_outlier)

    def test_classification_sensu_outliers_dedupes(self) -> None:
        rows = [
            {
                "class_event_id": 1,
                "class_term_id": 99,
                "sensu_term": "(Presl)",
                "classterm": "Trisetum flavescens (L.) P.Beauv.",
            },
            {
                "class_event_id": 1,
                "class_term_id": 99,
                "sensu_term": "(Presl)",
                "classterm": "Trisetum flavescens (L.) P.Beauv.",
            },
            {
                "class_event_id": 2,
                "class_term_id": 100,
                "sensu_term": "s.lat.",
                "classterm": "Rosa L.",
            },
        ]
        outliers = classification_sensu_outliers(rows)
        self.assertEqual(len(outliers), 1)
        self.assertEqual(outliers[0]["sensu_term"], "(Presl)")


if __name__ == "__main__":
    unittest.main()
