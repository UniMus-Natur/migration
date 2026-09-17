"""Unit tests for unplaced-place geography GUID detection."""

from __future__ import annotations

import unittest

from flows.lib.cleanup_unplaced_geography import is_unplaced_place_geography_guid


class UnplacedPlaceGeographyGuidTests(unittest.TestCase):
    def test_placeholder_guid(self) -> None:
        self.assertTrue(
            is_unplaced_place_geography_guid(
                "urn:oracle:musit_botanikk_felles:hpo:place2265916"
            )
        )

    def test_real_hierarchy_guid_is_not_placeholder(self) -> None:
        self.assertFalse(
            is_unplaced_place_geography_guid(
                "urn:oracle:musit_botanikk_felles:hpo:2486"
            )
        )
        self.assertFalse(
            is_unplaced_place_geography_guid(
                "urn:oracle:musit_botanikk_felles:hpo:4437"
            )
        )

    def test_earth_and_empty(self) -> None:
        self.assertFalse(
            is_unplaced_place_geography_guid("urn:migration:geography-root:treedef-2")
        )
        self.assertFalse(is_unplaced_place_geography_guid(None))
        self.assertFalse(is_unplaced_place_geography_guid(""))


if __name__ == "__main__":
    unittest.main()
