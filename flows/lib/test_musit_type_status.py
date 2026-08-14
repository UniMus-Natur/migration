"""Unit tests for MUSIT typification type → Specify typeStatusName mapping."""

from __future__ import annotations

import unittest

from flows.lib.musit_type_status import resolve_type_status_name


class MusitTypeStatusTests(unittest.TestCase):
    def test_epitype_passthrough(self) -> None:
        self.assertEqual(resolve_type_status_name("Epitype"), "Epitype")

    def test_hyphenated_exholotype(self) -> None:
        self.assertEqual(resolve_type_status_name("Ex-holotype"), "Exholotype")

    def test_blank_and_none(self) -> None:
        self.assertIsNone(resolve_type_status_name(None))
        self.assertIsNone(resolve_type_status_name("  "))


if __name__ == "__main__":
    unittest.main()
