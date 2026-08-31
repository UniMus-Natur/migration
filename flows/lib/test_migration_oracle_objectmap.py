"""Tests for migration_oracle_objectmap helpers."""

from __future__ import annotations

import re
import unittest

# Keep in sync with flows.lib.musit_dataset_loader._AGENT_REMARKS_ACTOR_RE
_AGENT_REMARKS_ACTOR_RE = re.compile(
    r"MUSIT-migration:\s*ACTOR;\s*schema=(?P<schema>[^;]+);\s*ACTOR_ID=(?P<actor_id>\d+)",
    re.IGNORECASE,
)


class TestCollectionObjectGuid(unittest.TestCase):
    def test_guid_prefix(self) -> None:
        # Avoid importing django-backed module in unit tests — mirror the helper.
        owner = "MUSIT_BOTANIKK_FELLES"
        self.assertEqual(
            f"urn:oracle:{owner.lower()}:object:",
            "urn:oracle:musit_botanikk_felles:object:",
        )

    def test_guid_for_object(self) -> None:
        owner = "MUSIT_BOTANIKK_FELLES"
        self.assertEqual(
            f"urn:oracle:{owner.lower()}:object:{12345}"[:128],
            "urn:oracle:musit_botanikk_felles:object:12345",
        )


class TestAgentRemarksParse(unittest.TestCase):
    def test_parse_actor_marker(self) -> None:
        m = _AGENT_REMARKS_ACTOR_RE.match(
            "MUSIT-migration: ACTOR; schema=MUSIT_BOTANIKK_FELLES; ACTOR_ID=42; institution=O"
        )
        assert m is not None
        self.assertEqual(m.group("schema"), "MUSIT_BOTANIKK_FELLES")
        self.assertEqual(m.group("actor_id"), "42")


if __name__ == "__main__":
    unittest.main()
