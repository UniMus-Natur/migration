"""Unit tests for MUSIT → Agent name selection (issue #74)."""

from __future__ import annotations

import unittest

from flows.lib.musit_agent_names import (
    choose_agent_name_parts,
    parse_actorname,
    preferred_person_name_id_sql,
)


class MusitAgentNamesTests(unittest.TestCase):
    def test_parse_actorname(self) -> None:
        first, last, middle = parse_actorname("Blytt, Mathias N.")
        self.assertEqual(last, "Blytt")
        self.assertEqual(first, "Mathias")
        self.assertEqual(middle, "N.")

    def test_choose_prefers_actorname(self) -> None:
        parts = choose_agent_name_parts(
            person_given_name="M. N.",
            person_surname="Bl.",
            person_middle_name=None,
            title=None,
            actorname="Blytt, Mathias N.",
            agent_type=1,
        )
        self.assertEqual(parts.lastname, "Blytt")
        self.assertEqual(parts.firstname, "Mathias")
        self.assertEqual(parts.middleinitial, "N.")

    def test_choose_actorname_wins_even_if_shorter(self) -> None:
        parts = choose_agent_name_parts(
            person_given_name="Mathias",
            person_surname="Blytt",
            person_middle_name="Numsen",
            title="Dr.",
            actorname="Bl., M. N.",
            agent_type=1,
        )
        self.assertEqual(parts.lastname, "Bl.")
        self.assertEqual(parts.firstname, "M.")
        self.assertEqual(parts.middleinitial, "N.")
        self.assertEqual(parts.title, "Dr.")

    def test_choose_falls_back_to_person_name(self) -> None:
        parts = choose_agent_name_parts(
            person_given_name="Mathias",
            person_surname="Blytt",
            person_middle_name="Numsen",
            title="Dr.",
            actorname=None,
            agent_type=1,
        )
        self.assertEqual(parts.lastname, "Blytt")
        self.assertEqual(parts.firstname, "Mathias")
        self.assertEqual(parts.middleinitial, "Numsen")
        self.assertEqual(parts.title, "Dr.")

    def test_preferred_person_name_id_sql_matches_actorname_first(self) -> None:
        sql = preferred_person_name_id_sql("MUSIT_BOTANIKK_FELLES")
        self.assertIn("ACTORNAME", sql)
        self.assertIn("UPPER(", sql)
        self.assertNotIn("VALID_PERSON_NAME_ID", sql)


if __name__ == "__main__":
    unittest.main()
