"""Normalize OAuth provider keys in ``spuserexternalid`` (e.g. feide → Feide)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from flows.lib.mariadb_logical_sync import MariaDBEndpoint, connect_endpoint

logger = logging.getLogger(__name__)

CANONICAL_FEIDE_PROVIDER = "Feide"


@dataclass
class ProviderNormalizeReport:
    label: str
    database: str
    table_present: bool
    noncanonical_count: int
    conflict_duplicates: int
    renamed: int
    deleted_duplicates: int
    dry_run: bool
    sample_providerids: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _table_exists(conn, schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
             LIMIT 1
            """,
            [schema, table],
        )
        return cur.fetchone() is not None


def normalize_feide_provider_key(
    ep: MariaDBEndpoint,
    *,
    dry_run: bool = True,
    canonical: str = CANONICAL_FEIDE_PROVIDER,
) -> ProviderNormalizeReport:
    """Rename non-canonical Feide provider rows to ``canonical`` on one DB.

    Conflict rule: if both ``feide`` and ``Feide`` exist for the same
    ``providerid``, keep the canonical row and drop the non-canonical duplicate.
    """
    schema = ep.database
    with connect_endpoint(ep) as conn:
        if not _table_exists(conn, schema, "spuserexternalid"):
            return ProviderNormalizeReport(
                label=ep.label,
                database=schema,
                table_present=False,
                noncanonical_count=0,
                conflict_duplicates=0,
                renamed=0,
                deleted_duplicates=0,
                dry_run=dry_run,
                sample_providerids=[],
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT providerid
                  FROM spuserexternalid
                 WHERE LOWER(provider) = LOWER(%s)
                   AND provider <> %s
                 ORDER BY id
                 LIMIT 20
                """,
                [canonical, canonical],
            )
            samples = [str(r[0]) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT COUNT(*)
                  FROM spuserexternalid
                 WHERE LOWER(provider) = LOWER(%s)
                   AND provider <> %s
                """,
                [canonical, canonical],
            )
            noncanonical = int(cur.fetchone()[0] or 0)

            cur.execute(
                """
                SELECT COUNT(*)
                  FROM spuserexternalid AS bad
                  JOIN spuserexternalid AS good
                    ON good.provider = %s
                   AND good.providerid = bad.providerid
                 WHERE LOWER(bad.provider) = LOWER(%s)
                   AND bad.provider <> %s
                """,
                [canonical, canonical, canonical],
            )
            conflicts = int(cur.fetchone()[0] or 0)

        renamed = 0
        deleted = 0
        if not dry_run and noncanonical:
            with conn.cursor() as cur:
                # Drop non-canonical rows that already have a canonical twin.
                cur.execute(
                    """
                    DELETE bad
                      FROM spuserexternalid AS bad
                      JOIN spuserexternalid AS good
                        ON good.provider = %s
                       AND good.providerid = bad.providerid
                     WHERE LOWER(bad.provider) = LOWER(%s)
                       AND bad.provider <> %s
                    """,
                    [canonical, canonical, canonical],
                )
                deleted = int(cur.rowcount or 0)

                cur.execute(
                    """
                    UPDATE spuserexternalid
                       SET provider = %s
                     WHERE LOWER(provider) = LOWER(%s)
                       AND provider <> %s
                    """,
                    [canonical, canonical, canonical],
                )
                renamed = int(cur.rowcount or 0)
            conn.commit()
            logger.info(
                "%s: feide provider normalize deleted=%s renamed=%s",
                ep.label,
                deleted,
                renamed,
            )
        elif dry_run:
            # What a live run would do.
            deleted = conflicts
            renamed = max(noncanonical - conflicts, 0)

        return ProviderNormalizeReport(
            label=ep.label,
            database=schema,
            table_present=True,
            noncanonical_count=noncanonical,
            conflict_duplicates=conflicts,
            renamed=renamed,
            deleted_duplicates=deleted,
            dry_run=dry_run,
            sample_providerids=samples,
        )


def canonicalize_provider(provider: str | None) -> str:
    """Map known Feide spellings to the canonical Helm/OIDC provider key."""
    if not provider:
        return ""
    if provider.lower() == CANONICAL_FEIDE_PROVIDER.lower():
        return CANONICAL_FEIDE_PROVIDER
    return provider
