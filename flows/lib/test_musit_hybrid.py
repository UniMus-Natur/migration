"""Unit tests for MUSIT hybrid classterm detection and parent archival."""

from __future__ import annotations

import unittest

from flows.lib.musit_hybrid import (
    classterm_looks_hybrid,
    classification_hybrid_archives,
    determination_is_hybrid,
    entered_taxon_name_for_determination,
    hybrid_parents_from_rows,
)


class MusitHybridTests(unittest.TestCase):
    def test_classterm_looks_hybrid(self) -> None:
        self.assertTrue(classterm_looks_hybrid("Salix alba x fragilis"))
        self.assertTrue(classterm_looks_hybrid("Salix alba × fragilis"))
        self.assertFalse(classterm_looks_hybrid("Salix alba"))
        self.assertFalse(classterm_looks_hybrid("Carex"))

    def test_parents_dedupe_and_order_by_formula(self) -> None:
        rows = [
            {
                "class_event_id": 1,
                "entered_classterm": "Salix acutifolia x daphnoides",
                "classterm": "Salix acutifolia Willd. x daphnoides Vill.",
                "latin_name_id": 256783,
                "latin_name": "daphnoides",
                "full_name": "Salix daphnoides",
                "adb_taxon_id": 62623,
                "precision_code": None,
                "relation_type": 92,
            },
            {
                "class_event_id": 1,
                "entered_classterm": "Salix acutifolia x daphnoides",
                "classterm": "Salix acutifolia Willd. x daphnoides Vill.",
                "latin_name_id": 256783,
                "latin_name": "daphnoides",
                "full_name": "Salix daphnoides",
                "adb_taxon_id": 218440,  # fan-out duplicate
                "precision_code": None,
                "relation_type": 92,
            },
            {
                "class_event_id": 1,
                "entered_classterm": "Salix acutifolia x daphnoides",
                "classterm": "Salix acutifolia Willd. x daphnoides Vill.",
                "latin_name_id": 264888,
                "latin_name": "acutifolia",
                "full_name": "Salix acutifolia",
                "adb_taxon_id": 86667,
                "precision_code": None,
                "relation_type": 92,
            },
        ]
        parents = hybrid_parents_from_rows(rows)
        self.assertEqual(len(parents), 2)
        self.assertEqual(parents[0]["latin_name"], "acutifolia")
        self.assertEqual(parents[1]["latin_name"], "daphnoides")
        self.assertEqual(parents[1]["adb_taxon_id"], 62623)

    def test_cf_precision_preserved(self) -> None:
        rows = [
            {
                "class_event_id": 2,
                "entered_classterm": "Salix alba x cf. fragilis x pentandra",
                "latin_name_id": 1,
                "latin_name": "alba",
                "full_name": "Salix alba",
                "precision_code": None,
            },
            {
                "class_event_id": 2,
                "entered_classterm": "Salix alba x cf. fragilis x pentandra",
                "latin_name_id": 2,
                "latin_name": "fragilis",
                "full_name": "Salix fragilis",
                "precision_code": "cf.",
            },
            {
                "class_event_id": 2,
                "entered_classterm": "Salix alba x cf. fragilis x pentandra",
                "latin_name_id": 3,
                "latin_name": "pentandra",
                "full_name": "Salix pentandra",
                "precision_code": None,
            },
        ]
        parents = hybrid_parents_from_rows(rows)
        self.assertEqual([p["precision_code"] for p in parents], [None, "cf.", None])
        self.assertTrue(determination_is_hybrid(rows[0], parents))

    def test_parents_display_includes_cf(self) -> None:
        from flows.lib.musit_hybrid import hybrid_parents_display

        text = hybrid_parents_display(
            [
                {"full_name": "Salix alba", "precision_code": None},
                {"full_name": "Salix fragilis", "precision_code": "cf."},
            ]
        )
        self.assertEqual(text, "Salix alba; Salix fragilis (cf.)")

    def test_archive_only_for_hybrids(self) -> None:
        rows = [
            {
                "class_event_id": 10,
                "class_term_id": 100,
                "entered_classterm": "Carex digitata",
                "classterm": "Carex digitata L.",
                "latin_name_id": 9,
                "latin_name": "digitata",
                "full_name": "Carex digitata",
            },
            {
                "class_event_id": 11,
                "class_term_id": 101,
                "entered_classterm": "Salix aurita x caprea",
                "classterm": "Salix aurita L. x caprea L.",
                "latin_name_id": 1,
                "latin_name": "aurita",
                "full_name": "Salix aurita",
                "adb_taxon_id": 1,
                "relation_type": 92,
            },
            {
                "class_event_id": 11,
                "class_term_id": 101,
                "entered_classterm": "Salix aurita x caprea",
                "classterm": "Salix aurita L. x caprea L.",
                "latin_name_id": 2,
                "latin_name": "caprea",
                "full_name": "Salix caprea",
                "adb_taxon_id": 2,
                "relation_type": 92,
            },
        ]
        archives = classification_hybrid_archives(rows)
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0]["class_event_id"], 11)
        self.assertEqual(len(archives[0]["parents"]), 2)
        self.assertEqual(
            entered_taxon_name_for_determination(rows[1]),
            "Salix aurita x caprea",
        )


if __name__ == "__main__":
    unittest.main()
