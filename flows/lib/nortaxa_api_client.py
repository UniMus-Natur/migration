"""HTTP client for the NorTaxa REST API (https://nortaxa.artsdatabanken.no).

Bulk discipline slices use ``DataTransfer/Export``; incremental sync uses ``TaxonName/ChangeLog``.
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://nortaxa.artsdatabanken.no"
USER_AGENT = "migration-prefect-nortaxa/2.0"

# Normalized TSV columns written by API extract (merge reads these + legacy DwC aliases).
NORTAXA_TSV_FIELDNAMES: list[str] = [
    "taxonID",
    "taxonId",
    "parentNameUsageID",
    "acceptedNameUsageID",
    "scientificName",
    "presentationName",
    "taxonRank",
    "taxonomicStatus",
    "scientificNameAuthorship",
    "vernacularNameBokmaal",
    "existsInNorway",
]

_EXPORT_COLUMNS = {
    "ScientificNameId": "taxonID",
    "TaxonId": "taxonId",
    "ParentScientificNameId": "parentNameUsageID",
    "AcceptedScientificNameId": "acceptedNameUsageID",
    "NameString": "scientificName",
    "PresentationName": "presentationName",
    "Rank": "taxonRank",
    "TaxonomicStatus": "taxonomicStatus",
    "Author": "scientificNameAuthorship",
    "VernacularNameBokmaal": "vernacularNameBokmaal",
    "ExistsInNorway": "existsInNorway",
}


@dataclass
class ChangeLogPage:
    """One page from ``/api/v1/TaxonName/ChangeLog``."""

    result: list[dict[str, Any]]
    next_cursor: str | None
    total_count: int | None


class NorTaxaApiClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout_s: int = 300,
        max_retries: int = 4,
        retry_backoff_s: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str | int | bool | None] | None = None,
        accept: str = "application/json",
    ) -> bytes:
        q = {k: str(v) for k, v in (query or {}).items() if v is not None and v != ""}
        url = f"{self.base_url}{path}"
        if q:
            url = f"{url}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(
            url,
            method=method,
            headers={"User-Agent": USER_AGENT, "Accept": accept},
        )
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return resp.read()
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_err = exc
                if attempt + 1 >= self.max_retries:
                    break
                time.sleep(self.retry_backoff_s * (attempt + 1))
        raise RuntimeError(f"NorTaxa API request failed: {method} {url}") from last_err

    def _get_json(self, path: str, query: dict[str, str | int | bool | None] | None = None) -> Any:
        raw = self._request("GET", path, query=query)
        return json.loads(raw.decode("utf-8"))

    def export_subtree_csv(
        self,
        scientific_name_id: str | int,
        *,
        include_synonyms: bool = True,
        include_vernacular_names: bool = True,
        export_type: str = "Csv",
    ) -> list[dict[str, str]]:
        """Download ``DataTransfer/Export`` for one root ``scientificNameId``."""
        raw = self._request(
            "GET",
            "/api/v1/DataTransfer/Export",
            query={
                "exportType": export_type,
                "fileName": f"nortaxa_{scientific_name_id}",
                "scientificNameId": int(scientific_name_id),
                "includeSynonyms": include_synonyms,
                "includeVernacularNames": include_vernacular_names,
            },
            accept="text/csv,*/*",
        )
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            out: dict[str, str] = {}
            for src_col, dst_col in _EXPORT_COLUMNS.items():
                val = (raw_row.get(src_col) or "").strip()
                out[dst_col] = val
            sid = out.get("taxonID", "")
            if sid:
                rows.append(out)
        return rows

    def fetch_taxon_by_scientific_name_id(self, scientific_name_id: str | int) -> dict[str, Any]:
        return self._get_json(f"/api/v1/TaxonName/ByScientificNameId/{int(scientific_name_id)}")

    def changelog_since(
        self,
        from_date: str,
        *,
        cursor: str | None = None,
        limit: int = 500,
        to_date: str | None = None,
    ) -> ChangeLogPage:
        """Paginated changelog from ``from_date`` (ISO date or datetime string)."""
        query: dict[str, str | int | None] = {"fromDate": from_date, "limit": limit}
        if cursor:
            query["nextCursor"] = cursor
        if to_date:
            query["toDate"] = to_date
        data = self._get_json("/api/v1/TaxonName/ChangeLog", query=query)
        if isinstance(data, list):
            return ChangeLogPage(result=data, next_cursor=None, total_count=len(data))
        return ChangeLogPage(
            result=list(data.get("result") or []),
            next_cursor=data.get("nextCursor"),
            total_count=data.get("totalCount"),
        )

    def changelog_recent(self) -> list[dict[str, Any]]:
        data = self._get_json("/api/v1/TaxonName/ChangeLog2")
        return list(data) if isinstance(data, list) else []

    def iter_changelog(
        self,
        from_date: str,
        *,
        limit: int = 500,
        max_pages: int | None = None,
    ):
        """Yield all changelog events from ``from_date`` until cursor exhausted."""
        cursor: str | None = None
        pages = 0
        while True:
            page = self.changelog_since(from_date, cursor=cursor, limit=limit)
            for event in page.result:
                yield event
            if not page.next_cursor:
                break
            cursor = page.next_cursor
            pages += 1
            if max_pages is not None and pages >= max_pages:
                break


def normalize_taxonomic_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s in ("accepted", "valid", ""):
        return "accepted"
    if s == "synonym":
        return "synonym"
    return s


def row_scientific_name_id(row: dict[str, str]) -> str:
    return (row.get("taxonID") or row.get("scientificNameId") or "").strip()


def row_parent_scientific_name_id(row: dict[str, str]) -> str:
    return (row.get("parentNameUsageID") or row.get("parentScientificNameId") or "").strip()


def row_accepted_scientific_name_id(row: dict[str, str]) -> str:
    return (row.get("acceptedNameUsageID") or row.get("acceptedScientificNameId") or "").strip()
