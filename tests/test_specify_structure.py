"""Tests for Specify structure sync (matching + YAML parsing)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flows.lib.specify_structure.config import load_structure_config
from flows.lib.specify_structure.matching import (
    collection_key,
    discipline_name_key,
    division_key,
    norm_code,
    norm_name,
)


class TestMatching(unittest.TestCase):
    def test_norm_name_strips(self) -> None:
        self.assertEqual(norm_name("  Foo  "), "Foo")

    def test_norm_code_strips(self) -> None:
        self.assertEqual(norm_code("  X  "), "X")

    def test_division_key_casefold(self) -> None:
        self.assertEqual(division_key(1, "Biology"), division_key(1, "biology"))

    def test_discipline_name_key_casefold(self) -> None:
        self.assertEqual(discipline_name_key("Alger"), discipline_name_key("alger"))

    def test_collection_key(self) -> None:
        self.assertEqual(collection_key(5, "NHM"), collection_key(5, "nhm"))


class TestLoadStructureConfig(unittest.TestCase):
    def test_loads_minimal_yaml(self) -> None:
        content = """
institution_name: "Test Org"
divisions:
  - name: DivOne
    disciplines:
      - name: DiscA
        type: botany
        collections:
          - { code: C1, name: Collection One }
"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.yaml"
            p.write_text(content, encoding="utf-8")
            cfg = load_structure_config(p)
        self.assertEqual(cfg.institution_name, "Test Org")
        self.assertEqual(len(cfg.divisions), 1)
        self.assertEqual(cfg.divisions[0].name, "DivOne")
        self.assertEqual(cfg.divisions[0].disciplines[0].type, "botany")
        self.assertEqual(cfg.divisions[0].disciplines[0].collections[0].code, "C1")

    def test_rejects_empty_institution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.yaml"
            p.write_text("divisions: []\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_structure_config(p)


if __name__ == "__main__":
    unittest.main()
