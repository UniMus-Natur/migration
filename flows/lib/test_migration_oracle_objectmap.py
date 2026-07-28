"""Tests for migration_oracle_objectmap helpers."""

from __future__ import annotations

import unittest

from flows.lib.migration_oracle_objectmap import (
    collectionobject_guid,
    collectionobject_guid_prefix,
)


class TestCollectionObjectGuid(unittest.TestCase):
    def test_guid_prefix(self) -> None:
        self.assertEqual(
            collectionobject_guid_prefix("MUSIT_BOTANIKK_FELLES"),
            "urn:oracle:musit_botanikk_felles:object:",
        )

    def test_guid_for_object(self) -> None:
        self.assertEqual(
            collectionobject_guid("MUSIT_BOTANIKK_FELLES", 12345),
            "urn:oracle:musit_botanikk_felles:object:12345",
        )


if __name__ == "__main__":
    unittest.main()
