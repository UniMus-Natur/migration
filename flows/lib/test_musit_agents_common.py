"""Unit tests for MUSIT agent remarks marker helpers."""

from __future__ import annotations

import unittest

from flows.lib.musit_agents_common import (
    build_musit_actor_remarks,
    extract_oracle_note_from_remarks,
    is_oracle_note_only_remarks,
    musit_actor_remarks_marker,
    parse_actor_id_from_agent_remarks,
    parse_actor_marker,
)


class MusitAgentsCommonTests(unittest.TestCase):
    def test_marker_roundtrip(self) -> None:
        marker = musit_actor_remarks_marker("MUSIT_BOTANIKK_FELLES", 42)
        self.assertEqual(parse_actor_marker(marker), ("MUSIT_BOTANIKK_FELLES", 42))
        self.assertEqual(
            parse_actor_id_from_agent_remarks(marker, "MUSIT_BOTANIKK_FELLES"),
            42,
        )

    def test_marker_with_oracle_note_suffix(self) -> None:
        remarks = build_musit_actor_remarks(
            "MUSIT_BOTANIKK_FELLES",
            99,
            institution="NHM",
            oracle_note="<FODT>1900</FODT>; extra",
        )
        self.assertEqual(parse_actor_marker(remarks), ("MUSIT_BOTANIKK_FELLES", 99))
        self.assertTrue(remarks.startswith("MUSIT-migration: ACTOR; schema="))
        self.assertIn("oracle_note=<FODT>1900</FODT>; extra", remarks)
        self.assertEqual(
            extract_oracle_note_from_remarks(remarks),
            "<FODT>1900</FODT>; extra",
        )

    def test_oracle_note_only_detection(self) -> None:
        mangled = "MUSIT-migration: ACTOR; oracle_note=<FODT>1789</FODT>"
        self.assertTrue(is_oracle_note_only_remarks(mangled))
        self.assertIsNone(parse_actor_marker(mangled))
        good = musit_actor_remarks_marker("MUSIT_BOTANIKK_FELLES", 1) + "; oracle_note=x"
        self.assertFalse(is_oracle_note_only_remarks(good))


if __name__ == "__main__":
    unittest.main()
