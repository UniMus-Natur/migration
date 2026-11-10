"""Unit tests for MUSIT REG_USER → cataloger helpers (no Django)."""

from __future__ import annotations

import unittest

from flows.lib.musit_cataloger import normalize_musit_login


class MusitCatalogerTests(unittest.TestCase):
    def test_normalize_musit_login(self) -> None:
        self.assertEqual(normalize_musit_login("PERLI"), "PERLI")
        self.assertEqual(normalize_musit_login("  perli "), "PERLI")
        self.assertIsNone(normalize_musit_login(None))
        self.assertIsNone(normalize_musit_login("   "))


if __name__ == "__main__":
    unittest.main()
