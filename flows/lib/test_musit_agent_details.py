"""Unit tests for MUSIT ACTOR details → Agent fill-in helpers."""

from __future__ import annotations

import unittest
from datetime import date

from flows.lib.musit_agent_details import (
    DATE_PRECISION_DAY,
    DATE_PRECISION_MONTH,
    DATE_PRECISION_YEAR,
    build_agent_details_patch,
    dates_from_actor_note,
    parse_partial_date,
    parse_url_note_identifiers,
    patch_is_noop,
)


class PartialDateTests(unittest.TestCase):
    def test_year_month_day(self) -> None:
        self.assertEqual(parse_partial_date("1819-01-01"), (date(1819, 1, 1), DATE_PRECISION_DAY))

    def test_year_month(self) -> None:
        self.assertEqual(parse_partial_date("1910-09"), (date(1910, 9, 1), DATE_PRECISION_MONTH))

    def test_year_only(self) -> None:
        self.assertEqual(parse_partial_date("1819"), (date(1819, 1, 1), DATE_PRECISION_YEAR))


class NoteDateTests(unittest.TestCase):
    def test_fodt_dod_tags(self) -> None:
        note = "<FODT>1819-01-01</FODT><DOD>1910-09"
        # Unclosed DOD in screenshot — also test well-formed:
        note2 = "<FODT>1819-01-01</FODT><DOD>1910-09</DOD>"
        birth, death = dates_from_actor_note(note2)
        self.assertEqual(birth, (date(1819, 1, 1), DATE_PRECISION_DAY))
        self.assertEqual(death, (date(1910, 9, 1), DATE_PRECISION_MONTH))
        # Malformed unclosed tag should not match _TAG_RE
        birth_bad, death_bad = dates_from_actor_note(note)
        self.assertEqual(birth_bad, (date(1819, 1, 1), DATE_PRECISION_DAY))
        self.assertEqual(death_bad, (None, None))


class UrlNoteIdentifierTests(unittest.TestCase):
    def test_wikidata_person_id_tag(self) -> None:
        raw = "<personID>https://www.wikidata.org/wiki/Q1525290</personID>"
        ids = parse_url_note_identifiers(raw)
        self.assertEqual(ids[0].identifier_type, "Wikidata")
        self.assertEqual(ids[0].identifier, "Q1525290")

    def test_orcid_url(self) -> None:
        ids = parse_url_note_identifiers("https://orcid.org/0000-0002-1825-0097")
        self.assertEqual(ids[0].identifier_type, "ORCID")
        self.assertEqual(ids[0].identifier, "0000-0002-1825-0097")


class PatchTests(unittest.TestCase):
    def _empty_agent(self) -> dict:
        return dict(
            agent_email=None,
            agent_url=None,
            agent_dob=None,
            agent_dod=None,
            agent_text1=None,
            agent_text2=None,
            agent_text3=None,
            agent_text4=None,
            agent_text5=None,
            agent_remarks="MUSIT-migration: ACTOR; schema=MUSIT_BOTANIKK_FELLES; ACTOR_ID=47891",
            has_address=False,
            existing_identifier_keys=set(),
        )

    def test_christen_smith_style_row(self) -> None:
        row = {
            "actor_id": 47891,
            "actorname": "Smith, Christen",
            "group_member_names": None,
            "birthdate": None,
            "deathdate": None,
            "adress": None,
            "postal_address": None,
            "email_address": None,
            "phone_number": None,
            "institution": None,
            "gender": None,
            "url": None,
            "url_note": "<personID>https://www.wikidata.org/wiki/Q1525290</personID>",
            "note": "<FODT>1819-01-01</FODT><DOD>1910-09</DOD>",
        }
        patch = build_agent_details_patch(row, **self._empty_agent())
        self.assertFalse(patch_is_noop(patch))
        self.assertEqual(patch.agent_fields["text1"], "Smith, Christen")
        self.assertEqual(patch.agent_fields["text5"], row["url_note"])
        self.assertEqual(patch.agent_fields["dateofbirth"], date(1819, 1, 1))
        self.assertEqual(patch.agent_fields["dateofdeath"], date(1910, 9, 1))
        self.assertEqual(patch.identifiers[0].identifier, "Q1525290")
        self.assertIn("oracle_note=", patch.remarks or "")

    def test_does_not_overwrite_existing(self) -> None:
        row = {
            "actor_id": 1,
            "actorname": "Other",
            "group_member_names": "g",
            "birthdate": date(1900, 1, 1),
            "deathdate": None,
            "adress": "Street 1",
            "postal_address": None,
            "email_address": "a@b.c",
            "phone_number": "123",
            "institution": "NHM",
            "gender": "M",
            "url": "http://example.com",
            "url_note": "<personID>https://www.wikidata.org/wiki/Q1</personID>",
            "note": "hello",
        }
        patch = build_agent_details_patch(
            row,
            agent_email="already@x.com",
            agent_url="http://kept",
            agent_dob=date(1800, 1, 1),
            agent_dod=None,
            agent_text1="kept display",
            agent_text2="kept group",
            agent_text3="kept gender",
            agent_text4="kept inst",
            agent_text5="kept url note",
            agent_remarks="MUSIT-migration: ACTOR; schema=X; ACTOR_ID=1; oracle_note=hello",
            has_address=True,
            existing_identifier_keys={("Wikidata", "Q1")},
        )
        self.assertNotIn("email", patch.agent_fields)
        self.assertNotIn("url", patch.agent_fields)
        self.assertNotIn("dateofbirth", patch.agent_fields)
        self.assertNotIn("text1", patch.agent_fields)
        self.assertFalse(patch.create_address)
        self.assertEqual(patch.identifiers, [])


if __name__ == "__main__":
    unittest.main()
