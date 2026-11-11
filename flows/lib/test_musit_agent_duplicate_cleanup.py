"""Unit tests for MUSIT agent duplicate cleanup planning."""

from __future__ import annotations

import unittest

from flows.lib.musit_agent_duplicate_cleanup import (
    AgentCleanupCandidate,
    keeper_sort_key,
    normalize_actorname,
    pick_keeper,
    plan_actor_cleanup,
)
from flows.lib.musit_agents_common import musit_actor_remarks_marker


def _cand(
    agent_id: int,
    *,
    remarks: str | None = None,
    text1: str | None = None,
    firstname: str | None = None,
    lastname: str | None = "X",
    variants: int = 0,
    addresses: int = 0,
) -> AgentCleanupCandidate:
    return AgentCleanupCandidate(
        agent_id=agent_id,
        remarks=remarks,
        firstname=firstname,
        lastname=lastname,
        middleinitial=None,
        title=None,
        text1=text1,
        variant_count=variants,
        address_count=addresses,
        identifier_count=0,
    )


class DuplicateCleanupPlanTests(unittest.TestCase):
    def test_normalize_actorname(self) -> None:
        self.assertEqual(normalize_actorname("  Blytt,  Mathias  "), "blytt, mathias")
        self.assertIsNone(normalize_actorname("  "))

    def test_pick_keeper_prefers_variants(self) -> None:
        orphan = _cand(10, variants=3, remarks="MUSIT-migration: ACTOR; oracle_note=x")
        dupe = _cand(
            999,
            remarks=musit_actor_remarks_marker("MUSIT_BOTANIKK_FELLES", 1),
            firstname="Mathias",
            lastname="Blytt",
        )
        self.assertEqual(pick_keeper([dupe, orphan]).agent_id, 10)
        self.assertGreater(keeper_sort_key(orphan), keeper_sort_key(dupe))

    def test_plan_restores_marker_and_deletes_dupe(self) -> None:
        schema = "MUSIT_BOTANIKK_FELLES"
        orphan = _cand(
            10,
            remarks="MUSIT-migration: ACTOR; oracle_note=<FODT>1789</FODT>",
            text1="Blytt, Mathias Numsen",
            firstname="M. N.",
            lastname="Bl.",
            variants=2,
        )
        dupe = _cand(
            900,
            remarks=musit_actor_remarks_marker(schema, 629),
            text1=None,
            firstname="Mathias",
            lastname="Blytt",
            variants=0,
        )
        plan = plan_actor_cleanup(
            schema=schema,
            actor_id=629,
            actorname="Blytt, Mathias Numsen",
            actor_type=0,
            candidates=[orphan, dupe],
        )
        assert plan is not None
        self.assertEqual(plan.keeper_id, 10)
        self.assertEqual(plan.delete_ids, [900])
        self.assertIsNotNone(plan.restore_remarks)
        assert plan.restore_remarks is not None
        self.assertTrue(
            plan.restore_remarks.startswith(
                musit_actor_remarks_marker(schema, 629)
            )
        )
        self.assertIn("oracle_note=<FODT>1789</FODT>", plan.restore_remarks)
        self.assertEqual(plan.name_updates.get("lastname"), "Blytt")
        self.assertEqual(plan.name_updates.get("firstname"), "Mathias")
        self.assertEqual(plan.name_updates.get("middleinitial"), "Numsen")


if __name__ == "__main__":
    unittest.main()
