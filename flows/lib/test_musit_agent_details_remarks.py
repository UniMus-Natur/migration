"""Unit tests: agent-details remarks must keep schema/ACTOR_ID marker."""

from __future__ import annotations

import unittest

from flows.lib.musit_agent_details import build_agent_details_patch
from flows.lib.musit_agents_common import (
    musit_actor_remarks_marker,
    parse_actor_marker,
)


class AgentDetailsRemarksTests(unittest.TestCase):
    def test_appends_oracle_note_without_stripping_marker(self) -> None:
        marker = musit_actor_remarks_marker("MUSIT_BOTANIKK_FELLES", 629)
        patch = build_agent_details_patch(
            {"actorname": "Blytt, Mathias Numsen", "note": "<FODT>1789</FODT>"},
            schema="MUSIT_BOTANIKK_FELLES",
            actor_id=629,
            agent_email=None,
            agent_url=None,
            agent_dob=None,
            agent_dod=None,
            agent_text1=None,
            agent_text2=None,
            agent_text3=None,
            agent_text4=None,
            agent_text5=None,
            agent_remarks=marker,
            has_address=True,
            existing_identifier_keys=set(),
        )
        self.assertIsNotNone(patch.remarks)
        assert patch.remarks is not None
        self.assertEqual(parse_actor_marker(patch.remarks), ("MUSIT_BOTANIKK_FELLES", 629))
        self.assertIn("oracle_note=<FODT>1789</FODT>", patch.remarks)

    def test_repairs_mangled_oracle_note_only_remarks(self) -> None:
        mangled = "MUSIT-migration: ACTOR; oracle_note=<FODT>1789</FODT>"
        patch = build_agent_details_patch(
            {"note": None},
            schema="MUSIT_BOTANIKK_FELLES",
            actor_id=629,
            agent_email=None,
            agent_url=None,
            agent_dob=None,
            agent_dod=None,
            agent_text1="Blytt, Mathias Numsen",
            agent_text2=None,
            agent_text3=None,
            agent_text4=None,
            agent_text5=None,
            agent_remarks=mangled,
            has_address=True,
            existing_identifier_keys=set(),
        )
        self.assertIsNotNone(patch.remarks)
        assert patch.remarks is not None
        self.assertEqual(parse_actor_marker(patch.remarks), ("MUSIT_BOTANIKK_FELLES", 629))
        self.assertIn("oracle_note=<FODT>1789</FODT>", patch.remarks)


if __name__ == "__main__":
    unittest.main()
