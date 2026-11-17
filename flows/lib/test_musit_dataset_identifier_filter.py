"""Catalog-number series filter used to split one MUSIT collection into two Specify collections."""

import unittest

from flows.lib.musit_identifier_series import identifier_num_filter_sql


class IdentifierNumFilterSqlTests(unittest.TestCase):
    def test_no_bounds_adds_no_sql(self) -> None:
        join_sql, where_sql, binds = identifier_num_filter_sql(
            oracle_schema="MUSIT_BOTANIKK_FELLES",
            identifier_num_at_least=None,
            identifier_num_below=None,
        )
        self.assertEqual(join_sql, "")
        self.assertEqual(where_sql, "")
        self.assertEqual(binds, {})

    def test_below_keeps_null_numbers(self) -> None:
        join_sql, where_sql, binds = identifier_num_filter_sql(
            oracle_schema="MUSIT_BOTANIKK_FELLES",
            identifier_num_at_least=None,
            identifier_num_below=2_000_000,
        )
        self.assertIn("museum_object mo", join_sql)
        self.assertIn("identifier_num IS NULL OR mo.identifier_num < :id_below", where_sql)
        self.assertEqual(binds, {"id_below": 2_000_000})

    def test_at_least_requires_a_number(self) -> None:
        _join_sql, where_sql, binds = identifier_num_filter_sql(
            oracle_schema="MUSIT_BOTANIKK_FELLES",
            identifier_num_at_least=2_000_000,
            identifier_num_below=None,
        )
        self.assertIn("mo.identifier_num >= :id_at_least", where_sql)
        self.assertNotIn("IS NULL", where_sql)
        self.assertEqual(binds, {"id_at_least": 2_000_000})


if __name__ == "__main__":
    unittest.main()
