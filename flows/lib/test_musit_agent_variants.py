"""Unit tests for MUSIT PERSON_NAME → AgentVariant helpers."""

from __future__ import annotations

import unittest

from flows.lib.musit_agent_variants import (
    format_person_name_variant,
    should_skip_variant_name,
    sql_alternate_person_names,
)
from flows.lib.musit_agents_common import (
    musit_actor_remarks_marker,
    parse_actor_id_from_agent_remarks,
)


class FormatPersonNameVariantTests(unittest.TestCase):
    def test_surname_and_given(self) -> None:
        self.assertEqual(
            format_person_name_variant("Bjørdal", "Inge"),
            "Bjørdal, Inge",
        )

    def test_with_middle(self) -> None:
        self.assertEqual(
            format_person_name_variant("Sørensen", "H.", "L."),
            "Sørensen, H. L.",
        )

    def test_surname_only(self) -> None:
        self.assertEqual(format_person_name_variant("Elven", None), "Elven")

    def test_given_only(self) -> None:
        self.assertEqual(format_person_name_variant(None, "R."), "R.")

    def test_empty(self) -> None:
        self.assertIsNone(format_person_name_variant(None, None))
        self.assertIsNone(format_person_name_variant("  ", "  "))


class SkipVariantTests(unittest.TestCase):
    def test_empty_and_duplicate(self) -> None:
        self.assertEqual(should_skip_variant_name(None, set()), "empty")
        self.assertEqual(should_skip_variant_name("A, B", {"A, B"}), "duplicate")
        self.assertIsNone(should_skip_variant_name("A, B", set()))


class RemarksMarkerTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        marker = musit_actor_remarks_marker("MUSIT_BOTANIKK_FELLES", 42)
        self.assertEqual(
            parse_actor_id_from_agent_remarks(marker, "MUSIT_BOTANIKK_FELLES"),
            42,
        )

    def test_with_extra_remarks_suffix(self) -> None:
        remarks = (
            musit_actor_remarks_marker("MUSIT_BOTANIKK_FELLES", 99)
            + "; institution=NHM"
        )
        self.assertEqual(
            parse_actor_id_from_agent_remarks(remarks, "MUSIT_BOTANIKK_FELLES"),
            99,
        )

    def test_wrong_schema(self) -> None:
        marker = musit_actor_remarks_marker("MUSIT_BOTANIKK_FELLES", 1)
        self.assertIsNone(
            parse_actor_id_from_agent_remarks(marker, "MUSIT_ZOOLOGI_ENTOMOLOGI")
        )


class SqlAlternateTests(unittest.TestCase):
    def test_excludes_preferred_person_name(self) -> None:
        sql = sql_alternate_person_names("MUSIT_BOTANIKK_FELLES")
        self.assertIn("VALID_PERSON_NAME_ID", sql)
        self.assertIn("PERSON_NAME_ID <>", sql)
        self.assertIn("MUSIT_BOTANIKK_FELLES.PERSON_NAME", sql)


if __name__ == "__main__":
    unittest.main()
