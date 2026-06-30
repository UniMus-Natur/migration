"""Reusable MUSIT specimen loader: Oracle → Specify 7 Django ORM.

This module is the single place that knows how to translate one Oracle MUSIT
``MUSEUM_OBJECT`` row (plus its connected event/place/taxon/agent rows) into the
Specify 7 record chain:

    CollectingEvent → CollectionObject → Determination(s)
                        ↑
                    Locality  (created on-the-fly via migration_oracle_placemap)
                        ↑
                    Geography (resolved by GUID; created on-the-fly under Earth using Specify ``Tree`` APIs)

Every future collection migration creates a ``MusitDatasetConfig`` and calls
``load_musit_dataset``; it does not need to know anything about Oracle SQL or
Specify model internals.

Design constraints
------------------
* All Specify writes use the **Django ORM** (``specifyweb.specify.models``).
* Bridge-table rows (objectmap, placemap) use raw SQL on ``django.db.connection``.
* Never creates or modifies ``Agent`` or ``Taxon``.
* Each specimen is wrapped in ``transaction.atomic`` so partial failures roll back
  cleanly without leaving orphan records.
* Idempotent: objects already in ``migration_oracle_objectmap`` are skipped on re-run.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timezone
from typing import Any

from django.db import close_old_connections, transaction

from flows.lib.musit_taxon_match import (
    binomial_prefix_from_valid_classterm as _binomial_prefix_from_valid_classterm,
    taxon_matches_valid_classterm as _taxon_matches_valid_classterm,
)

logger = logging.getLogger(__name__)

# How often to emit a progress line (number of objects processed).
_PROGRESS_EVERY = 100

# Cap error strings saved in stats to avoid huge memory / report blobs.
_MAX_ERRORS = 200

# Oracle IN-list batch size for SPECIMEN_SQL.
_SPECIMEN_BATCH = 500


# ---------------------------------------------------------------------------
# Configuration dataclass (one per collection / dataset)
# ---------------------------------------------------------------------------


@dataclass
class MusitDatasetConfig:
    """All collection-specific knobs in one place.

    Create one of these per dataset and pass it to ``load_musit_dataset``.
    """

    oracle_schema: str           # e.g. "MUSIT_BOTANIKK_FELLES"
    institutioncode: str         # e.g. "O"
    collectioncode: str          # e.g. "V"
    specify_collection_code: str # e.g. "O-V"
    specify_discipline_name: str # e.g. "Karplanter Moser"
    dataset_label: str           # e.g. "oslo-vascular-v1" (written into JSON payload)


# ---------------------------------------------------------------------------
# Stats dataclass
# ---------------------------------------------------------------------------


@dataclass
class DatasetLoadStats:
    co_created: int = 0
    co_skipped: int = 0         # already in objectmap → idempotent skip
    ce_created: int = 0
    locality_created: int = 0
    locality_reused: int = 0    # found in placemap
    geography_created: int = 0
    determination_created: int = 0
    taxon_matched: int = 0
    taxon_fallback_matched: int = 0
    taxon_unresolved: int = 0
    agent_matched: int = 0
    agent_unresolved: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    estimate_total_s: float | None = None


# ---------------------------------------------------------------------------
# Oracle SQL
# ---------------------------------------------------------------------------

# Phase 1 — page OBJECT_IDs in stable sorted order.
_PAGE_SQL = """
    SELECT voa.object_id
      FROM {schema}.v_object_attributes voa
     WHERE voa.institutioncode = :icode
       AND voa.collectioncode  = :ccode
     ORDER BY voa.object_id
     OFFSET :skip ROWS FETCH NEXT :batch ROWS ONLY
"""

# Phase 2 — one query per page: full multi-join envelope.
# Rows are grouped in Python by object_id.  Because of the LEFT JOINs a single
# object may appear on multiple rows (multiple determinations or actors).
_SPECIMEN_SQL = """
    SELECT
      voa.object_id,
      oa.uuid,
      mo.identifier_string,
      mo.long_name,
      mo.identifier_num,
      mo.parent_object_id,
      mo.mediagruppe_enhets_id,
      oa.is_reg,
      oa.is_approved,
      oa.is_corrected,
      oa.object_withheld,
      oa.object_state,
      oa.reg_user,
      oa.korr_user,
      oa.approve_user,
      oa.reg_date,
      oa.approved_date,
      oa.korr_date,
      oa.last_modified,
      oa.last_modified_by,
      oa.dataset,
      oa.project_name,
      oa.same_sheet_as,
      oa.dublettes,
      oa.analysis_request,
      oa.number_of_sheets,
      oa.ex_herb,
      oa.voucher,
      oa.artsobs_nr,
      mon.object_notes,
      en.event_notes,
      (SELECT MIN(mlp.legnr)
         FROM {schema}.museum_object_legnr_person mlp
        WHERE mlp.object_id = voa.object_id
          AND mlp.legnr IS NOT NULL
      ) AS legnr,
      (SELECT MIN(tye.type_status)
         FROM {schema}.event_museum_object emo2
         JOIN {schema}.typification_event tye
           ON tye.event_id = emo2.event_id
        WHERE emo2.object_id = voa.object_id
          AND tye.type_status IS NOT NULL
      ) AS type_status,
      emo.sequence_number,
      emo.prev_event_for_objekt,
      ev.event_type,
      ev.eventname,
      ce.event_id,
      ce.collectiontype_id,
      ce.legname_orig,
      ce.agg_personnames,
      ts.from_date,
      ts.to_date,
      ts.time_as_text,
      ts.uncertain         AS date_uncertain,
      por.place_id,
      lp.locality          AS locality_text,
      kp.coordinate_string,
      kp.latitude_l,
      kp.longitude_l,
      kp.datum,
      cte.classification_type_id,
      cte.event_id         AS class_event_id,
      cts.from_date        AS class_from_date,
      cts.to_date          AS class_to_date,
      cts.time_as_text     AS class_time_as_text,
      cts.uncertain        AS class_date_uncertain,
      ct.classterm,
      ct.entered_classterm,
      ct.valid_classterm,
      ct.infraspes_name,
      ct.sensu_term,
      tcat.tax_cath_name   AS infraspes_rank_name,
      ln.latin_name_id,
      ln.latin_name,
      ln.full_name,
      ln.full_name_author,
      ln.parent_latin_name_id,
      ln.nhm_taxon_id,
      COALESCE(ln.adb_latin_name_id, labd.adb_latin_name) AS adb_latin_name_id,
      -- NorTaxa id: prefer CLASSIFICATION_TAXON → TAXON; valid_latin_name path can disagree.
      COALESCE(tx.adb_taxon_id, txv.adb_taxon_id) AS adb_taxon_id,
      ln.tax_cath_id,
      ln.is_valid          AS taxon_is_valid,
      erp.actor_id,
      erp.role_id,
      (SELECT MIN(era.actor_id)
         FROM {schema}.event_role_actor era
        WHERE era.event_id = cte.event_id
      ) AS classification_actor_id,
      (SELECT MIN(pn.actor_id)
         FROM {schema}.event_role_person_name erpn
         JOIN {schema}.person_name pn
           ON pn.person_name_id = erpn.person_name_id
        WHERE erpn.event_id = cte.event_id
      ) AS classification_person_name_actor_id,
      cte.detname_orig,
      cte.agg_personnames  AS det_agg_personnames
    FROM {schema}.v_object_attributes voa
    JOIN {schema}.object_attributes oa
      ON oa.object_id = voa.object_id
    JOIN {schema}.museum_object mo
      ON mo.object_id = voa.object_id
    LEFT JOIN (
      SELECT mon_in.object_id, LISTAGG(n_in.note_text, '; ') WITHIN GROUP (ORDER BY mon_in.museum_object_note_id) AS object_notes
        FROM {schema}.museum_object_note mon_in
        JOIN {schema}.note n_in ON n_in.note_id = mon_in.note_id
       GROUP BY mon_in.object_id
    ) mon ON mon.object_id = voa.object_id
    LEFT JOIN {schema}.event_museum_object emo
      ON emo.object_id = voa.object_id
    LEFT JOIN {schema}.event ev
      ON ev.event_id = emo.event_id
    LEFT JOIN {schema}.collecting_event ce
      ON ce.event_id = emo.event_id
    LEFT JOIN (
      SELECT en_in.event_id, LISTAGG(n_in.note_text, '; ') WITHIN GROUP (ORDER BY en_in.event_note_id) AS event_notes
        FROM {schema}.event_note en_in
        JOIN {schema}.note n_in ON n_in.note_id = en_in.note_id
       GROUP BY en_in.event_id
    ) en ON en.event_id = emo.event_id
    LEFT JOIN {schema}.timespan ts
      ON ts.timespan_id = ev.timespan_id
    LEFT JOIN {schema}.place_event_role por
      ON por.event_id = ce.event_id
    LEFT JOIN {schema}.place_locality_place plp
      ON plp.place_id = por.place_id
    LEFT JOIN (
      SELECT locality_place_id, locality
        FROM {schema}.locality_place
       WHERE locality_place_id IN (
         SELECT MIN(locality_place_id)
           FROM {schema}.locality_place
          GROUP BY locality_place_id
       )
    ) lp ON lp.locality_place_id = plp.locality_place_id
    LEFT JOIN {schema}.koordinate_place_place kpp
      ON kpp.place_id = por.place_id
    LEFT JOIN {schema}.koordinate_place kp
      ON kp.koordinate_place_id = kpp.koordinate_place_id
    LEFT JOIN {schema}.classification_event cte
      ON cte.event_id = emo.event_id
    LEFT JOIN {schema}.event cev
      ON cev.event_id = cte.event_id
    LEFT JOIN {schema}.timespan cts
      ON cts.timespan_id = cev.timespan_id
    LEFT JOIN {schema}.classification_term ct
      ON ct.class_term_id = cte.class_term_id
    LEFT JOIN {schema}.taxon_cathegory tcat
      ON tcat.tax_cath_id = ct.infraspes_rank
    LEFT JOIN {schema}.classterm_latin_name ctl
      ON ctl.classterm_id = ct.class_term_id
    LEFT JOIN {schema}.latin_names ln
      ON ln.latin_name_id = ctl.latin_name_id
    LEFT JOIN {schema}.latinname_adb labd
      ON labd.latin_name_id = ln.latin_name_id
    LEFT JOIN {schema}.taxon txv
      ON txv.valid_latin_name_id = ln.latin_name_id
    LEFT JOIN {schema}.classification_taxon ctax
      ON ctax.class_term_id = ct.class_term_id
    LEFT JOIN {schema}.taxon tx
      ON REGEXP_LIKE(TRIM(ctax.tax_id), '^[0-9]+$')
     AND tx.taxon_id = TO_NUMBER(TRIM(ctax.tax_id))
    LEFT JOIN {schema}.event_role_actor erp
      ON erp.event_id = ce.event_id
    WHERE voa.object_id IN ({placeholders})
    ORDER BY voa.object_id, emo.sequence_number NULLS LAST
"""


# ---------------------------------------------------------------------------
# Logging helper (prefer Prefect run logger)
# ---------------------------------------------------------------------------


def _log(level: str, msg: str, *args: Any) -> None:
    try:
        from prefect import get_run_logger
        getattr(get_run_logger(), level)(msg, *args)
    except Exception:
        getattr(logger, level)(msg, *args)


def _format_duration(seconds: float) -> str:
    if seconds != seconds or seconds < 0:
        return "?"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    return f"{s // 3600}h{(s % 3600) // 60}m"


# ---------------------------------------------------------------------------
# Specify lookup helpers
# ---------------------------------------------------------------------------


def _resolve_taxon(
    adb_taxon_id: Any,
    adb_latin_name_id: Any,
    nhm_taxon_id: Any,
    latin_name: str | None,
    taxontreedef_id: int,
) -> Any:
    """Look up an existing Specify ``Taxon`` row by NorTaxa scientific name id."""
    from specifyweb.specify.models import Taxon

    taxon_fields = ("id", "name", "author", "source", "fullname", "taxonomicserialnumber")
    for raw_id in (adb_taxon_id, adb_latin_name_id):
        if raw_id is None:
            continue
        try:
            serial = str(int(raw_id))
        except (TypeError, ValueError):
            continue
        t = (
            Taxon.objects.filter(
                taxonomicserialnumber=serial,
                definition_id=taxontreedef_id,
            )
            .only(*taxon_fields)
            .first()
        )
        if t is not None:
            return t

    return None


def _find_taxon_by_valid_classterm(
    *,
    taxontreedef_id: int,
    valid_classterm: str | None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Fallback match on ``valid_classterm`` against Specify ``Taxon`` rows.

    NorTaxa-managed trees store rank-local epithets in ``Taxon.name`` and binomials
    in ``fullname`` (via ``set_fullnames``), so both fields are searched.

    1. Case-insensitive exact match on ``Taxon.name`` or ``Taxon.fullname``.
    2. When the probe looks like a name with authorship, match the leading binomial
       against ``name`` or ``fullname`` and require ``_taxon_matches_valid_classterm``.
    """
    from django.db.models import Q

    from specifyweb.specify.models import Taxon

    probe = " ".join((valid_classterm or "").strip().split())
    if not probe:
        return None, []

    taxon_fields = ("id", "name", "author", "source", "fullname")

    def _row_dict(taxon: Any, *, match_mode: str) -> dict[str, Any]:
        return {
            "id": int(taxon.id),
            "name": taxon.name,
            "author": getattr(taxon, "author", None),
            "source": getattr(taxon, "source", None),
            "fullname": getattr(taxon, "fullname", None),
            "probe": probe,
            "match_mode": match_mode,
        }

    def _single_or_none(
        matches: list[Any],
        *,
        match_mode: str,
    ) -> tuple[Any, list[dict[str, Any]]]:
        rows = [_row_dict(t, match_mode=match_mode) for t in matches]
        if len(matches) == 1:
            return matches[0], rows
        return None, rows

    def _query_field(field: str, value: str) -> list[Any]:
        return list(
            Taxon.objects.filter(
                definition_id=taxontreedef_id,
                **{f"{field}__iexact": value},
            ).only(*taxon_fields)
        )

    def _try_exact(*, match_mode: str) -> tuple[Any, list[dict[str, Any]]] | None:
        by_name = _query_field("name", probe)
        if len(by_name) == 1:
            return by_name[0], [_row_dict(by_name[0], match_mode=f"{match_mode}:name")]
        by_fullname = _query_field("fullname", probe)
        if len(by_fullname) == 1:
            return by_fullname[0], [_row_dict(by_fullname[0], match_mode=f"{match_mode}:fullname")]
        combined = list(
            Taxon.objects.filter(
                definition_id=taxontreedef_id,
            )
            .filter(Q(name__iexact=probe) | Q(fullname__iexact=probe))
            .only(*taxon_fields)
        )
        guarded = [t for t in combined if _taxon_matches_valid_classterm(t, valid_classterm)]
        found, rows = _single_or_none(guarded, match_mode=f"{match_mode}+guardrail")
        if found is not None or guarded:
            return found, rows
        if combined:
            return None, [_row_dict(t, match_mode=match_mode) for t in combined]
        return None

    exact_hit = _try_exact(match_mode="exact_iexact")
    if exact_hit is not None:
        return exact_hit

    binomial = _binomial_prefix_from_valid_classterm(probe)
    if binomial:
        binomial_hits = list(
            Taxon.objects.filter(
                definition_id=taxontreedef_id,
            )
            .filter(Q(name__iexact=binomial) | Q(fullname__iexact=binomial))
            .only(*taxon_fields)
        )
        guarded = [t for t in binomial_hits if _taxon_matches_valid_classterm(t, valid_classterm)]
        found, rows = _single_or_none(guarded, match_mode="binomial+guardrail")
        if found is not None:
            return found, rows
        if guarded or binomial_hits:
            return None, rows

    return None, []


def _fetch_latin_name_lineage(
    *,
    oracle_cursor: Any,
    owner: str,
    latin_name_id: int,
    lineage_cache: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return Latin-name lineage from root to leaf for one ``LATIN_NAME_ID``."""
    o = owner.upper()
    lineage_leaf_to_root: list[dict[str, Any]] = []
    current = int(latin_name_id)
    seen: set[int] = set()

    while current not in seen:
        seen.add(current)
        row = lineage_cache.get(current)
        if row is None:
            oracle_cursor.execute(
                f"""
                SELECT
                  ln.latin_name_id,
                  ln.parent_latin_name_id,
                  ln.latin_name,
                  ln.full_name,
                  ln.full_name_author,
                  ln.nhm_taxon_id,
                  COALESCE(ln.adb_latin_name_id, labd.adb_latin_name) AS adb_latin_name_id,
                  tx.adb_taxon_id,
                  tc.tax_cath_code,
                  tc.tax_cath_name
                FROM {o}.latin_names ln
                LEFT JOIN {o}.latinname_adb labd
                  ON labd.latin_name_id = ln.latin_name_id
                LEFT JOIN {o}.taxon tx
                  ON tx.valid_latin_name_id = ln.latin_name_id
                LEFT JOIN {o}.taxon_cathegory tc
                  ON tc.tax_cath_id = ln.tax_cath_id
                WHERE ln.latin_name_id = :lnid
                """,
                {"lnid": current},
            )
            rec = oracle_cursor.fetchone()
            if not rec:
                break
            row = {
                "latin_name_id": rec[0],
                "parent_latin_name_id": rec[1],
                "latin_name": rec[2],
                "full_name": rec[3],
                "full_name_author": rec[4],
                "nhm_taxon_id": rec[5],
                "adb_latin_name_id": rec[6],
                "adb_taxon_id": rec[7],
                "tax_cath_code": rec[8],
                "tax_cath_name": rec[9],
            }
            lineage_cache[current] = row
        lineage_leaf_to_root.append(row)
        parent = row.get("parent_latin_name_id")
        if parent is None:
            break
        try:
            current = int(parent)
        except (TypeError, ValueError):
            break

    return list(reversed(lineage_leaf_to_root))


def _pick_taxon_rank_item(
    *,
    taxontreedef_id: int,
    parent_rankid: int,
    tax_cath_code: str | None,
    tax_cath_name: str | None,
    rank_item_cache: dict[int, dict[str, Any]],
) -> Any:
    from specifyweb.specify.models import Taxontreedefitem

    if taxontreedef_id not in rank_item_cache:
        rank_item_cache[taxontreedef_id] = {
            "ordered": list(Taxontreedefitem.objects.filter(treedef_id=taxontreedef_id).order_by("rankid")),
            "by_name": {},
        }
        by_name: dict[str, Any] = {}
        for it in rank_item_cache[taxontreedef_id]["ordered"]:
            nm = (it.name or "").strip().lower()
            if nm and nm not in by_name:
                by_name[nm] = it
        rank_item_cache[taxontreedef_id]["by_name"] = by_name

    by_name = rank_item_cache[taxontreedef_id]["by_name"]
    ordered = rank_item_cache[taxontreedef_id]["ordered"]

    raw = f"{tax_cath_code or ''} {tax_cath_name or ''}".strip().lower()
    aliases = {
        "art": "species",
        "species": "species",
        "subspecies": "subspecies",
        "underart": "subspecies",
        "varietas": "variety",
        "variety": "variety",
        "genus": "genus",
        "slekt": "genus",
        "familie": "family",
        "family": "family",
        "orden": "order",
        "order": "order",
        "klasse": "class",
        "class": "class",
        "fylum": "phylum",
        "phylum": "phylum",
        "rike": "kingdom",
        "kingdom": "kingdom",
    }
    logical = aliases.get(raw) or aliases.get((tax_cath_code or "").strip().lower()) or aliases.get((tax_cath_name or "").strip().lower())

    if logical and logical in by_name and int(by_name[logical].rankid) > parent_rankid:
        return by_name[logical]

    # Unknown/empty MUSIT rank: prefer Species when legal; otherwise pick the deepest legal rank.
    species_item = by_name.get("species")
    if species_item is not None and int(species_item.rankid) > parent_rankid:
        return species_item
    candidates = [it for it in ordered if int(it.rankid) > parent_rankid]
    return candidates[-1] if candidates else None


def _resolve_or_create_taxon(
    *,
    oracle_cursor: Any,
    owner: str,
    latin_name_id: Any,
    adb_taxon_id: Any,
    adb_latin_name_id: Any,
    nhm_taxon_id: Any,
    latin_name: str | None,
    valid_classterm: str | None,
    full_name: str | None,
    full_name_author: str | None,
    taxontreedef_id: int,
    lineage_cache: dict[int, dict[str, Any]],
    rank_item_cache: dict[int, dict[str, Any]],
    object_id: int,
    catalog_number: str | None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Resolve taxon by NorTaxa id first, then conservative fallback on name.

    No automatic taxon creation in this path.
    """
    created_nodes: list[dict[str, Any]] = []

    existing = _resolve_taxon(
        adb_taxon_id=adb_taxon_id,
        adb_latin_name_id=adb_latin_name_id,
        nhm_taxon_id=nhm_taxon_id,
        latin_name=latin_name,
        taxontreedef_id=taxontreedef_id,
    )
    if existing is not None:
        # ``taxonomicserialnumber`` (= Oracle ``ADB_TAXON_ID``) is authoritative.
        if not _taxon_matches_valid_classterm(existing, valid_classterm):
            created_nodes.append(
                {
                    "mismatch_reason": "id_match_conflicts_with_valid_classterm",
                    "resolved_taxon_id": int(existing.id),
                    "resolved_taxon_name": getattr(existing, "name", None),
                    "resolved_taxon_fullname": getattr(existing, "fullname", None),
                    "valid_classterm": valid_classterm,
                    "object_id": object_id,
                    "catalog": catalog_number,
                }
            )
        return existing, created_nodes

    fallback, candidate_scores = _find_taxon_by_valid_classterm(
        taxontreedef_id=taxontreedef_id,
        valid_classterm=valid_classterm,
    )
    if fallback is not None:
        return fallback, created_nodes

    # carry candidate debug info for unresolved logging
    created_nodes.append(
        {
            "fallback_probe": " ".join((valid_classterm or "").strip().split()),
            "fallback_candidates": candidate_scores[:10],
            "object_id": object_id,
            "catalog": catalog_number,
            "latin_name_id": latin_name_id,
            "adb_taxon_id": adb_taxon_id,
            "adb_latin_name_id": adb_latin_name_id,
            "nhm_taxon_id": nhm_taxon_id,
        }
    )
    return None, created_nodes


def _resolve_agent(schema: str, actor_id: Any) -> Any:
    """Look up an existing Specify ``Agent`` by remarks marker; never creates one."""
    if actor_id is None:
        return None
    from specifyweb.specify.models import Agent

    sch = str(schema).strip().upper()
    marker = f"MUSIT-migration: ACTOR; schema={sch}; ACTOR_ID={int(actor_id)}"
    return Agent.objects.filter(remarks__startswith=marker).first()


def _determination_dedupe_key(r: dict[str, Any]) -> tuple:
    return (
        r.get("adb_taxon_id"),
        r.get("adb_latin_name_id"),
        r.get("latin_name_id"),
        _trunc(r.get("valid_classterm"), 255),
        _trunc(r.get("classterm"), 255),
    )


def _first_non_null_classification_determiner_actor_id(
    det_rows: list[dict[str, Any]], det_key: tuple
) -> Any:
    """Within rows sharing a determination dedupe key, pick a classification-event actor.

    Oracle join order can leave ``classification_*_actor_id`` null on the first row even when
    another row for the same determination envelope has a value.
    """
    for r in det_rows:
        if _determination_dedupe_key(r) != det_key:
            continue
        aid = r.get("classification_actor_id")
        if aid is not None:
            return aid
    for r in det_rows:
        if _determination_dedupe_key(r) != det_key:
            continue
        aid = r.get("classification_person_name_actor_id")
        if aid is not None:
            return aid
    return None


def _oracle_row_is_nominal_world_shell(r: dict[str, Any]) -> bool:
    """True when this hierarchical row is MUSIT's synthetic global shell (WORLD / Verden / …).

    Those nodes must not become a separate ``Geography`` under Earth (they would pick the
    Continent rank when ``TYPES`` is NULL).  Alias the Oracle HIERARCH_PLACE_ID to Earth instead.
    """
    from flows.lib.oracle_geography_load import _NULL_ORACLE_TYPE_REDUNDANT_PARENT_NAMES, _norm_type

    return _norm_type(r.get("name")) in _NULL_ORACLE_TYPE_REDUNDANT_PARENT_NAMES


def _fetch_hierarchical_chain_rows_for_place(
    oracle_cursor: Any,
    owner: str,
    place_id: int,
) -> list[dict[str, Any]]:
    """Return hierarchical rows for a PLACE_ID, including ancestors up to root."""
    from flows.lib.oracle_geography_load import _types_label_column_name

    o = owner.upper()
    label_col = _types_label_column_name(oracle_cursor, owner)
    type_expr = f"t.{label_col}" if label_col else "CAST(NULL AS VARCHAR2(4000))"

    oracle_cursor.execute(
        f"SELECT php.HIERACHICAL_PLACE_ID FROM {o}.place_hierachical_place php WHERE php.place_id = :pid",
        {"pid": place_id},
    )
    seed_ids = [int(r[0]) for r in oracle_cursor.fetchall() if r and r[0] is not None]
    if not seed_ids:
        return []

    by_hid: dict[int, dict[str, Any]] = {}
    queue: list[int] = list(seed_ids)
    seen: set[int] = set()
    while queue:
        hid = int(queue.pop())
        if hid in seen:
            continue
        seen.add(hid)
        oracle_cursor.execute(
            f"""
            SELECT h.HIERARCH_PLACE_ID, h.HIERACHICAL_PLACENAME, h.PLACE_ID_PARTOF, {type_expr} AS TYPE_NAME
              FROM {o}.hierarchical_place_old h
              LEFT JOIN {o}.types t ON t.TYPE_ID = h.HIERACHICAL_TYPE
             WHERE h.HIERARCH_PLACE_ID = :hid
            """,
            {"hid": hid},
        )
        row = oracle_cursor.fetchone()
        if not row:
            continue
        partof = int(row[2]) if row[2] is not None else None
        by_hid[hid] = {
            "hid": hid,
            "name": ((row[1] or "").strip() or f"ID_{hid}")[:128],
            "partof": partof,
            "type_name": row[3],
        }
        if partof is not None and partof not in seen:
            queue.append(partof)

    # Parent-before-child topological order.
    ordered: list[dict[str, Any]] = []
    remaining = set(by_hid.keys())
    ordered_ids: set[int] = set()
    guard = 0
    while remaining and guard < (len(remaining) + 5):
        guard += 1
        progressed = False
        for hid in list(remaining):
            parent = by_hid[hid]["partof"]
            if parent is None or parent not in by_hid or parent in ordered_ids:
                ordered.append(by_hid[hid])
                ordered_ids.add(hid)
                remaining.remove(hid)
                progressed = True
        if not progressed:
            for hid in list(remaining):
                ordered.append(by_hid[hid])
                ordered_ids.add(hid)
                remaining.remove(hid)
    return ordered


def _fix_geography_root_nodenumber_if_needed(earth: Any) -> None:
    """Specify ``Tree.adding_node`` requires ``parent.nodenumber``; roots created outside the
    workbench often have ``nodenumber`` / ``highestchildnodenumber`` NULL until the tree is
    initialized.  Without this, ``children.create`` raises ``AttributeError`` inside tree code.
    """
    from specifyweb.specify.models import Geography

    earth.refresh_from_db()
    if earth.nodenumber is not None and earth.highestchildnodenumber is not None:
        return
    nchild = Geography.objects.filter(parent_id=earth.id).count()
    if nchild > 0:
        raise RuntimeError(
            f"Geography root id={earth.id} has NULL node numbers but already has {nchild} child rows; "
            "repair the geography tree in Specify (or renumber) before migrating."
        )
    Geography.objects.filter(pk=earth.id).update(nodenumber=1, highestchildnodenumber=1)


def _ensure_earth_root_for_treedef(*, treedef_id: int, dry_run: bool) -> Any:
    """Ensure an Earth root exists for this GeographyTreeDef."""
    from specifyweb.specify.models import Geography

    from flows.lib.oracle_geography_load import _treedef_items_ordered_by_rank

    earth = Geography.objects.filter(definition_id=treedef_id, parent_id__isnull=True).order_by("id").first()
    if earth is not None:
        if not dry_run:
            _fix_geography_root_nodenumber_if_needed(earth)
        return earth
    if dry_run:
        return None

    ordered_items = _treedef_items_ordered_by_rank(treedef_id)
    if not ordered_items:
        return None
    root_item = ordered_items[0]
    with transaction.atomic():
        g = Geography.objects.create(
            name="Earth",
            fullname="Earth",
            definition_id=treedef_id,
            definitionitem=root_item,
            parent=None,
            rankid=root_item.rankid,
            isaccepted=True,
            iscurrent=True,
            guid=None,
        )
    _fix_geography_root_nodenumber_if_needed(g)
    return g


def _ensure_geography_for_place(
    *,
    oracle_cursor: Any,
    owner: str,
    place_id: int,
    geography_treedef_id: int,
    dry_run: bool,
    oracle_hid_to_geo: dict[int, int],
    geo_rankid_by_pk: dict[int, int],
    stats: DatasetLoadStats,
) -> int | None:
    """Create missing Geography chain for this place and return deepest geo id."""
    from specifyweb.specify.models import Geography

    from flows.lib.oracle_geography_load import (
        HierRow,
        _deepest_geography_for_place,
        _effective_parent_geography_for_untyped,
        _fetch_place_text,
        _rank_items_by_name_lower,
        _resolve_rank_item,
        _treedef_items_ordered_by_rank,
        oracle_type_name_to_rank_item_name,
    )

    earth = _ensure_earth_root_for_treedef(treedef_id=geography_treedef_id, dry_run=dry_run)
    if earth is None:
        return None
    _erk = getattr(earth, "rankid", None)
    geo_rankid_by_pk[int(earth.id)] = int(_erk) if _erk is not None else 0

    rank_items = _rank_items_by_name_lower(geography_treedef_id)
    ordered_items = _treedef_items_ordered_by_rank(geography_treedef_id)
    guid_prefix = f"urn:oracle:{owner.lower()}:hpo:"

    rows = _fetch_hierarchical_chain_rows_for_place(oracle_cursor, owner, place_id)
    if not rows:
        # No hierarchical_place_old chain for this PLACE_ID — attach a single leaf under Earth
        # so Locality always has a valid Geography (Specify tree code requires numbered roots).
        if dry_run:
            return None
        if len(ordered_items) < 2:
            raise RuntimeError(
                f"GeographyTreeDef {geography_treedef_id} has no ranks below Earth — cannot create place fallback."
            )
        agg, loc_text = _fetch_place_text(oracle_cursor, owner, place_id)
        name = ((loc_text or agg or f"Place {place_id}").strip() or f"Place {place_id}")[:128]
        guid = f"{guid_prefix}place{place_id}"
        existing = Geography.objects.filter(definition_id=geography_treedef_id, guid=guid).first()
        if existing is not None:
            return int(existing.id)
        leaf_di = ordered_items[1]
        fullname = f"Earth, {name}"[:500]
        with transaction.atomic():
            earth.refresh_from_db()
            g = earth.children.create(
                name=name,
                fullname=fullname,
                definition_id=geography_treedef_id,
                definitionitem=leaf_di,
                rankid=leaf_di.rankid,
                isaccepted=True,
                iscurrent=True,
                guid=guid,
            )
        stats.geography_created += 1
        return int(g.id)

    geo_cache: dict[int, Any] = {int(earth.id): earth}

    def _geo(pk: int) -> Any:
        if pk not in geo_cache:
            g = Geography.objects.filter(pk=pk).first()
            if g is not None:
                geo_cache[pk] = g
        return geo_cache.get(pk)

    for r in rows:
        hid = int(r["hid"])
        guid = f"{guid_prefix}{hid}"

        # Synthetic WORLD / Verden shell — map Oracle id to Specify Earth, do not insert a row.
        # Must run before the GUID reuse branch so a mistaken continent "WORLD" from an older run
        # is not re-linked via ``oracle_hid_to_geo``.
        if _oracle_row_is_nominal_world_shell(r):
            if not dry_run:
                oracle_hid_to_geo[hid] = int(earth.id)
                geo_cache[int(earth.id)] = earth
                _e = getattr(earth, "rankid", None)
                geo_rankid_by_pk[int(earth.id)] = int(_e) if _e is not None else 0
            continue

        existing = Geography.objects.filter(definition_id=geography_treedef_id, guid=guid).first()
        if existing is not None:
            oracle_hid_to_geo[hid] = int(existing.id)
            _er = getattr(existing, "rankid", None)
            geo_rankid_by_pk[int(existing.id)] = int(_er) if _er is not None else 0
            geo_cache[int(existing.id)] = existing
            continue

        parent_geo = earth
        parent_hid = r["partof"]
        if parent_hid is not None:
            parent_geo_id = oracle_hid_to_geo.get(int(parent_hid))
            if parent_geo_id is not None:
                parent_geo = _geo(parent_geo_id) or earth

        hier = HierRow(hid, r["name"], r["partof"], r.get("type_name"))
        parent_geo = _effective_parent_geography_for_untyped(parent_geo, hier, earth)

        # Earth has rankid=0 — never use ``(rankid or -1)`` here: 0 is falsy and would become -1,
        # then ``next(rankid > -1)`` picks the *Earth* treedef item again → duplicate rank 0 under
        # Earth → Specify Tree validation fails (and their error payload crashes on parent.parent).
        _pr = getattr(parent_geo, "rankid", None)
        parent_rankid = int(_pr) if _pr is not None else -1
        logical = oracle_type_name_to_rank_item_name(r["type_name"])
        di = _resolve_rank_item(rank_items, logical) if logical else None
        if di is None or int(di.rankid) <= parent_rankid:
            di = next((it for it in ordered_items if int(it.rankid) > parent_rankid), None)
        if di is None:
            raise RuntimeError(
                f"No GeographyTreeDefItem rank above parent rankid={parent_rankid} for "
                f"owner={owner!r} place_id={place_id} hid={hid!r} name={r.get('name')!r}"
            )

        if dry_run:
            continue

        name = r["name"]
        parent_full = (getattr(parent_geo, "fullname", None) or getattr(parent_geo, "name", "Earth"))
        fullname = f"{parent_full}, {name}"[:500]
        with transaction.atomic():
            parent_geo.refresh_from_db()
            g = parent_geo.children.create(
                name=name,
                fullname=fullname,
                definition_id=geography_treedef_id,
                definitionitem=di,
                rankid=di.rankid,
                isaccepted=True,
                iscurrent=True,
                guid=guid,
            )
        gid = int(g.id)
        oracle_hid_to_geo[hid] = gid
        _gr = getattr(g, "rankid", None)
        geo_rankid_by_pk[gid] = int(_gr) if _gr is not None else 0
        geo_cache[gid] = g
        stats.geography_created += 1

    return _deepest_geography_for_place(
        oracle_cursor,
        owner,
        place_id,
        oracle_hid_to_geo,
        geo_rankid_by_pk,
    )


def _get_or_create_locality(
    *,
    place_id: int,
    oracle_cursor: Any,
    owner: str,
    discipline_id: int,
    geography_treedef_id: int,
    run_ts: str,
    dry_run: bool,
    locality_cache: dict[tuple[int, int], int],  # (discipline_id, place_id) → locality pk
    stats: DatasetLoadStats,
    biogeo_region: str | None = None,
) -> Any:
    """Return an existing or newly created Specify ``Locality`` for this Oracle PLACE_ID.

    Uses ``migration_oracle_placemap`` for persistence and ``locality_cache`` (per-run
    in-memory dict) to avoid repeat DB lookups within the same flow run.
    """
    from specifyweb.specify.models import Geography, Locality

    from flows.lib.migration_oracle_placemap import upsert_placemap_row
    from flows.lib.oracle_geography_load import (
        _deepest_geography_for_place,
        _fetch_first_coordinate,
        _fetch_place_text,
        _place_locality_guid,
        locality_spatial_kwargs_from_musit_koordinate,
    )

    if oracle_cursor is None:
        raise RuntimeError("_get_or_create_locality requires oracle_cursor (must not be None).")

    cache_key = (discipline_id, place_id)
    if cache_key in locality_cache:
        stats.locality_reused += 1
        return Locality.objects.filter(pk=locality_cache[cache_key]).first()

    # Check placemap for an existing locality created by a previous run.
    from django.db import connection as _conn
    from flows.lib.migration_oracle_placemap import TABLE_NAME as _PM_TABLE

    owner_upper = owner.upper()
    with _conn.cursor() as cur:
        cur.execute(
            f"SELECT specify_locality_id FROM {_PM_TABLE}"
            " WHERE source_owner=%s AND source_kind=%s AND source_id=%s AND specify_discipline_id=%s",
            [owner_upper, "place", str(place_id), discipline_id],
        )
        row = cur.fetchone()
    if row and row[0]:
        lid = int(row[0])
        locality_cache[cache_key] = lid
        stats.locality_reused += 1
        return Locality.objects.filter(pk=lid).first()

    # Need to create a new Locality.
    agg, loc_text = _fetch_place_text(oracle_cursor, owner, place_id)
    coord = _fetch_first_coordinate(oracle_cursor, owner, place_id)

    # Rebuild geo rank map (lightweight — only the set already in memory).
    owner_lower = owner.lower()
    geo_guid_prefix = f"urn:oracle:{owner_lower}:hpo:"
    geo_rankid_by_pk: dict[int, int] = dict(
        Geography.objects.filter(
            definition_id=geography_treedef_id,
            guid__startswith=geo_guid_prefix,
        ).values_list("id", "rankid")
    )
    # Build hid→geo_id for this place's hierarchical nodes.
    oracle_hid_to_geo: dict[int, int] = {}
    for g in Geography.objects.filter(
        definition_id=geography_treedef_id,
        guid__startswith=geo_guid_prefix,
    ).values("id", "guid"):
        tail = g["guid"][len(geo_guid_prefix):]
        if tail.isdigit():
            oracle_hid_to_geo[int(tail)] = int(g["id"])

    geo_id = _deepest_geography_for_place(
        oracle_cursor, owner, place_id, oracle_hid_to_geo, geo_rankid_by_pk
    )
    if geo_id is None:
        geo_id = _ensure_geography_for_place(
            oracle_cursor=oracle_cursor,
            owner=owner,
            place_id=place_id,
            geography_treedef_id=geography_treedef_id,
            dry_run=dry_run,
            oracle_hid_to_geo=oracle_hid_to_geo,
            geo_rankid_by_pk=geo_rankid_by_pk,
            stats=stats,
        )
    if geo_id is None:
        raise RuntimeError(
            f"Geography unresolved after ensure step: owner={owner!r} place_id={place_id} "
            f"treedef_id={geography_treedef_id}"
        )

    locality_name = (loc_text or agg or f"Place {place_id}")[:1024]
    verbatim = agg[:8192] if agg else None
    guid = _place_locality_guid(owner_lower, place_id, discipline_id)

    if dry_run:
        stats.locality_created += 1
        return None

    try:
        loc_kwargs: dict[str, Any] = {
            "discipline_id": discipline_id,
            "localityname": locality_name,
            "geography_id": geo_id,
            "srclatlongunit": 0,
            "guid": guid,
        }
        loc_kwargs.update(locality_spatial_kwargs_from_musit_koordinate(coord))
        if verbatim:
            loc_kwargs["text1"] = verbatim
        if biogeo_region:
            loc_kwargs["text2"] = _trunc(biogeo_region, 65535)  # Biogeographic region

        loc = Locality(**loc_kwargs)
        loc.save()
        lid = int(loc.id)
        locality_cache[cache_key] = lid
        stats.locality_created += 1

        upsert_placemap_row(
            source_owner=owner_upper,
            source_kind="place",
            source_id=str(place_id),
            specify_geography_id=geo_id,
            specify_locality_id=lid,
            specify_discipline_id=discipline_id,
            run_ts=run_ts,
            dry_run=False,
        )
        return loc
    except Exception as exc:  # noqa: BLE001
        _log("error", "Failed to create Locality for place_id=%s disc=%s: %s", place_id, discipline_id, exc)
        raise


# ---------------------------------------------------------------------------
# Row grouping
# ---------------------------------------------------------------------------


def _group_rows_by_object_id(rows: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for row in rows:
        oid = int(row["object_id"])
        out.setdefault(oid, []).append(row)
    return out


def _fetch_media_rows_for_group(oracle_cursor: Any, media_group_id: int) -> list[dict[str, Any]]:
    """Return URL-addressable media rows for one ``MEDIAGRUPPE_ENHETS_ID``.

    URL-only mode: we do not migrate binary files, only links.
    """
    oracle_cursor.execute(
        """
        SELECT mf.MEDIAFIL_ID, mf.OPPRINNELIG_FILNAVN, mf.ID_I_SAMLING, mf.TITTEL, mf.FORMAT, mf.MEDIA_TYPE
          FROM USD_FELLES.MEDIA_FIL mf
         WHERE mf.MEDIAGRUPPE_ENHETS_ID = :gid
           AND (mf.MEDIA_TYPE = '1' OR mf.MEDIA_TYPE IS NULL)
         ORDER BY mf.MEDIAFIL_ID
        """,
        {"gid": int(media_group_id)},
    )
    out: list[dict[str, Any]] = []
    for r in oracle_cursor.fetchall():
        out.append(
            {
                "mediafil_id": r[0],
                "opprinnelig_filnavn": r[1],
                "id_i_samling": r[2],
                "tittel": r[3],
                "format": r[4],
                "media_type": r[5],
            }
        )
    return out


def _unimus_media_url(*, media_group_id: int) -> str:
    """Public Unimus image endpoint keyed by ``MEDIAGRUPPE_ENHETS_ID``."""
    return f"https://www.unimus.no/felles/bilder/web_hent_bilde.php?id={int(media_group_id)}&type=jpeg"


def _pick_primary_media_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose metadata source when several ``MEDIA_FIL`` rows share one group URL."""
    for r in rows:
        orig = (r.get("opprinnelig_filnavn") or "").strip().lower()
        if orig.endswith((".tif", ".tiff")):
            return r
    return rows[0]


def _attach_media_urls_to_collection_object(
    *,
    oracle_cursor: Any,
    co: Any,
    media_group_id: int | None,
) -> int:
    """Create one URL attachment per media group (Unimus ``id=`` endpoint)."""
    if media_group_id is None:
        return 0

    from specifyweb.specify.models import Attachment, Collectionobjectattachment

    rows = _fetch_media_rows_for_group(oracle_cursor, int(media_group_id))
    if not rows:
        return 0

    table_id = int(getattr(getattr(co, "specify_model", None), "tableId", 1) or 1)
    r = _pick_primary_media_row(rows)
    mfid = r.get("mediafil_id")
    url = _unimus_media_url(media_group_id=int(media_group_id))
    orig = (
        (r.get("opprinnelig_filnavn") or "").strip()
        or (r.get("id_i_samling") or "").strip()
        or f"mediagruppe_{media_group_id}.jpg"
    )
    guessed, _ = mimetypes.guess_type(orig)
    fmt = (r.get("format") or "").strip().lower()
    mime = guessed or ("image/tiff" if fmt in {"tif", "tiff"} else "image/jpeg")
    title = ((r.get("tittel") or "").strip() or orig)[:255]
    mediafil_ids = ",".join(str(x.get("mediafil_id")) for x in rows if x.get("mediafil_id") is not None)

    att = Attachment(
        attachmentlocation=url[:128],
        origfilename=orig,
        title=title,
        mimetype=mime,
        tableid=table_id,
        ispublic=True,
        remarks=f"MUSIT media_group_id={int(media_group_id)} mediafil_ids={mediafil_ids}",
    )
    att.save()
    Collectionobjectattachment.objects.create(
        attachment=att,
        collectionobject=co,
        collectionmemberid=int(co.collectionmemberid),
        ordinal=1,
    )
    return 1


def _coerce_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val if not isinstance(val, datetime) else val.date()
    try:
        if isinstance(val, str):
            return datetime.fromisoformat(val).date()
    except (TypeError, ValueError):
        pass
    return None


def _coerce_datetime_with_optional_time(d: date | None, time_text: Any) -> datetime | None:
    if d is None:
        return None
    if time_text is None:
        return datetime.combine(d, dtime.min)
    s = str(time_text).strip()
    if not s:
        return datetime.combine(d, dtime.min)
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if not m:
        return datetime.combine(d, dtime.min)
    try:
        hh = int(m.group(1))
        mm = int(m.group(2))
        ss = int(m.group(3)) if m.group(3) is not None else 0
        return datetime.combine(d, dtime(hour=hh, minute=mm, second=ss))
    except ValueError:
        return datetime.combine(d, dtime.min)


def _trunc(s: Any, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    return (t[:max_len] if len(t) > max_len else t) or None


def _musit_bool(val: Any) -> bool | None:
    """Map a MUSIT flag column ('1'/'Y'/'TRUE' vs '0'/'N'/null) to a tri-state bool."""
    if val is None:
        return None
    s = str(val).strip().upper()
    if s == "":
        return None
    if s in ("1", "Y", "TRUE", "T"):
        return True
    if s in ("0", "N", "FALSE", "F"):
        return False
    return None


def _short_date(val: Any) -> str | None:
    """Format a MUSIT date value as ``YYYY-MM-DD`` for display-only audit text."""
    d = _coerce_date(val)
    if d is not None:
        return d.isoformat()
    return _trunc(val, 32)


def _is_collecting_row(row: dict[str, Any]) -> bool:
    """True when this join row carries a MUSIT ``COLLECTING_EVENT`` (``ce.event_id``)."""
    return row.get("event_id") is not None


def _select_primary_collecting_row(rows: list[dict]) -> dict | None:
    """Pick the terminal collecting event for this object.

    Uses highest ``EVENT_MUSEUM_OBJECT.SEQUENCE_NUMBER`` among collecting rows,
    tie-breaking toward rows that have a linked ``place_id``.
    """
    collecting = [r for r in rows if _is_collecting_row(r)]
    if not collecting:
        return None
    return max(
        collecting,
        key=lambda r: (
            int(r.get("sequence_number") or 0),
            1 if r.get("place_id") is not None else 0,
        ),
    )


def _object_scalar_row(rows: list[dict]) -> dict:
    """Museum-object scalar fields are identical on every join row."""
    return rows[0]


def _collecting_event_candidates(rows: list[dict]) -> list[dict[str, Any]]:
    """All collecting-event row variants, deduped by ``event_id``."""
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not _is_collecting_row(row):
            continue
        eid = row.get("event_id")
        if eid in seen:
            continue
        seen.add(eid)
        out.append(
            {
                "event_id": eid,
                "sequence_number": row.get("sequence_number"),
                "prev_event_for_objekt": row.get("prev_event_for_objekt"),
                "event_type": row.get("event_type"),
                "eventname": row.get("eventname"),
                "place_id": row.get("place_id"),
                "locality_text": row.get("locality_text"),
                "from_date": str(row["from_date"]) if row.get("from_date") is not None else None,
                "to_date": str(row["to_date"]) if row.get("to_date") is not None else None,
                "time_as_text": row.get("time_as_text"),
            }
        )
    return out


def _fetch_collector_actor_ids_for_event(
    oracle_cursor: Any,
    schema: str,
    event_id: int,
) -> list[int]:
    """Return ordered unique collector ``actor_id`` values for one collecting event."""
    sch = str(schema).strip().upper()
    ordered: list[int] = []
    seen: set[int] = set()

    oracle_cursor.execute(
        f"""
        SELECT pn.actor_id
          FROM {sch}.event_role_person_name erpn
          JOIN {sch}.person_name pn
            ON pn.person_name_id = erpn.person_name_id
         WHERE erpn.event_id = :eid
           AND pn.actor_id IS NOT NULL
         ORDER BY erpn.sorting_sequence NULLS LAST, erpn.event_person_name_role_id
        """,
        {"eid": int(event_id)},
    )
    for (actor_id,) in oracle_cursor.fetchall():
        aid = int(actor_id)
        if aid not in seen:
            seen.add(aid)
            ordered.append(aid)

    oracle_cursor.execute(
        f"""
        SELECT era.actor_id
          FROM {sch}.event_role_actor era
         WHERE era.event_id = :eid
           AND era.actor_id IS NOT NULL
         ORDER BY era.event_actor_role_id
        """,
        {"eid": int(event_id)},
    )
    for (actor_id,) in oracle_cursor.fetchall():
        aid = int(actor_id)
        if aid not in seen:
            seen.add(aid)
            ordered.append(aid)

    return ordered


def _attach_collectors_to_collecting_event(
    *,
    owner: str,
    collecting_event_id: int,
    ce: Any,
    oracle_cursor: Any,
    object_id: int,
    stats: DatasetLoadStats,
) -> int:
    """Create ``Collector`` rows for all resolved agents on a collecting event."""
    from specifyweb.specify.models import Collector

    actor_ids = _fetch_collector_actor_ids_for_event(
        oracle_cursor, owner, collecting_event_id
    )
    created = 0
    for idx, actor_id in enumerate(actor_ids):
        agent = _resolve_agent(owner, actor_id)
        if agent is None:
            stats.agent_unresolved += 1
            continue
        stats.agent_matched += 1
        try:
            Collector.objects.create(
                agent=agent,
                collectingevent=ce,
                isprimary=(idx == 0),
                ordernumber=idx + 1,
            )
            created += 1
        except Exception as exc:  # noqa: BLE001
            _log(
                "warning",
                "object_id=%s: collector link failed actor_id=%s: %s",
                object_id,
                actor_id,
                exc,
            )
    return created


# ---------------------------------------------------------------------------
# Per-object Specify write
# ---------------------------------------------------------------------------


def _write_one_object(
    *,
    object_id: int,
    rows: list[dict],
    config: MusitDatasetConfig,
    collection: Any,
    discipline: Any,
    oracle_cursor: Any,
    run_ts: str,
    dry_run: bool,
    locality_cache: dict[tuple[int, int], int],
    stats: DatasetLoadStats,
) -> None:
    """Write one CollectionObject + CollectingEvent + Determination(s) for ``object_id``.

    All ORM writes are inside a single ``transaction.atomic`` block.
    """
    from specifyweb.specify.models import (
        Collectingevent,
        Collectionobject,
        Determination,
    )

    from flows.lib.migration_oracle_objectmap import upsert_objectmap_row

    obj_row = _object_scalar_row(rows)
    collecting_row = _select_primary_collecting_row(rows)
    owner = config.oracle_schema

    # ---- Unmapped payload for JSON archival ----
    unmapped: dict[str, Any] = {}
    for key in (
        "long_name", "identifier_num", "parent_object_id", "mediagruppe_enhets_id",
        "is_reg", "is_approved", "is_corrected", "object_state",
        "reg_user", "korr_user", "approve_user", "dataset", "project_name",
        "same_sheet_as", "dublettes", "analysis_request",
    ):
        v = obj_row.get(key)
        if v is not None:
            unmapped[key] = v if not isinstance(v, (date, datetime)) else str(v)
    ce_unmapped_source = collecting_row or obj_row
    for key in ("collectiontype_id", "date_uncertain", "coordinate_string", "datum"):
        v = ce_unmapped_source.get(key)
        if v is not None:
            unmapped[key] = v if not isinstance(v, (date, datetime)) else str(v)

    # Collecting-event locality variants for audit (collecting rows only).
    locality_candidates: list[dict[str, Any]] = []
    seen_locality_keys: set[tuple[Any, ...]] = set()
    for idx, row in enumerate(rows):
        if not _is_collecting_row(row):
            continue
        place_id = row.get("place_id")
        locality_text = row.get("locality_text")
        if place_id is None and not locality_text:
            continue
        entry: dict[str, Any] = {
            "row_index": idx,
            "event_id": row.get("event_id"),
            "sequence_number": row.get("sequence_number"),
            "place_id": place_id,
            "locality_text": locality_text,
            "from_date": str(row["from_date"]) if row.get("from_date") is not None else None,
            "to_date": str(row["to_date"]) if row.get("to_date") is not None else None,
            "time_as_text": row.get("time_as_text"),
            "date_uncertain": row.get("date_uncertain"),
            "coordinate_string": row.get("coordinate_string"),
            "datum": row.get("datum"),
        }
        dedupe_key = (
            entry.get("event_id"),
            entry.get("place_id"),
            entry.get("locality_text"),
            entry.get("from_date"),
            entry.get("to_date"),
            entry.get("time_as_text"),
            entry.get("coordinate_string"),
            entry.get("datum"),
        )
        if dedupe_key in seen_locality_keys:
            continue
        seen_locality_keys.add(dedupe_key)
        locality_candidates.append(entry)

    selected_source: dict[str, Any] = {
        "selection_rule": "primary_collecting_event_highest_sequence",
        "event_id": None,
        "place_id": None,
        "locality_text": None,
        "sequence_number": None,
        "event_type": None,
        "eventname": None,
    }
    if collecting_row is not None:
        selected_source.update(
            {
                "event_id": collecting_row.get("event_id"),
                "place_id": collecting_row.get("place_id"),
                "locality_text": collecting_row.get("locality_text"),
                "sequence_number": collecting_row.get("sequence_number"),
                "event_type": collecting_row.get("event_type"),
                "eventname": collecting_row.get("eventname"),
            }
        )

    json_payload = json.dumps(
        {
            "source": {
                "owner": owner,
                "object_id": object_id,
                "dataset": config.dataset_label,
            },
            "unmapped": unmapped,
            "collecting_event_candidates": _collecting_event_candidates(rows),
            "locality_candidates": locality_candidates,
            "selected_locality_source": selected_source,
            "migration_meta": {
                "exported_at_utc": run_ts,
                "mapping_version": config.dataset_label,
            },
        },
        ensure_ascii=False,
        default=str,
    )

    with transaction.atomic():
        # 1. Locality (resolve or create on-the-fly) — failures abort the whole object write.
        from flows.lib.oracle_geography_load import _fetch_place_ecology_and_region

        locality = None
        habitat_text: str | None = None  # MUSIT ECOLOGY_PLACE → CollectingEvent habitat
        place_id_raw = collecting_row.get("place_id") if collecting_row else None
        if place_id_raw is not None:
            place_id = int(place_id_raw)
            if discipline.geographytreedef_id is None:
                raise RuntimeError(
                    f"Discipline id={discipline.id!r} has no geographytreedef_id — "
                    "initialize geography in Specify before migrating localities."
                )
            habitat_text, biogeo_region = _fetch_place_ecology_and_region(
                oracle_cursor, owner, place_id
            )
            locality = _get_or_create_locality(
                place_id=place_id,
                oracle_cursor=oracle_cursor,
                owner=owner,
                discipline_id=int(discipline.id),
                geography_treedef_id=int(discipline.geographytreedef_id),
                run_ts=run_ts,
                dry_run=dry_run,
                locality_cache=locality_cache,
                stats=stats,
                biogeo_region=biogeo_region,
            )

        # 2. CollectingEvent
        start_date = _coerce_date(collecting_row.get("from_date")) if collecting_row else None
        end_date = _coerce_date(collecting_row.get("to_date")) if collecting_row else None
        verbatim_date = (
            _trunc(collecting_row.get("time_as_text"), 50) if collecting_row else None
        )
        verbatim_locality = None
        if collecting_row:
            verbatim_locality = _trunc(
                collecting_row.get("locality_text") or collecting_row.get("legname_orig"),
                255,
            )
        collector_str = (
            _trunc(collecting_row.get("agg_personnames"), 255) if collecting_row else None
        )
        event_notes = collecting_row.get("event_notes") if collecting_row else None
        collecting_event_id = collecting_row.get("event_id") if collecting_row else None

        ce_guid = (
            f"urn:oracle:{owner.lower()}:event:{collecting_event_id or object_id}"[:128]
        )

        ce_kwargs: dict[str, Any] = {
            "discipline": discipline,
            "locality": locality,
            "guid": ce_guid,
        }
        if start_date:
            ce_kwargs["startdate"] = start_date
            ce_kwargs["startdateprecision"] = 1
        if end_date:
            ce_kwargs["enddate"] = end_date
        if verbatim_date:
            ce_kwargs["verbatimdate"] = verbatim_date
        if verbatim_locality:
            ce_kwargs["verbatimlocality"] = verbatim_locality

        # Collector number (MUSIT "Leg no") → stationFieldNumber.
        legnr = _trunc(obj_row.get("legnr"), 50)
        if legnr:
            ce_kwargs["stationfieldnumber"] = legnr

        # Habitat / ecology (MUSIT ECOLOGY_PLACE) → text1.
        if habitat_text:
            ce_kwargs["text1"] = _trunc(habitat_text, 65535)

        ce_remarks_parts = []
        if event_notes:
            ce_remarks_parts.append(f"Notes: {event_notes}")
        if ce_remarks_parts:
            ce_kwargs["remarks"] = _trunc("; ".join(ce_remarks_parts), 4000)

        if dry_run:
            stats.ce_created += 1
            stats.co_created += 1
            det_rows = [
                r
                for r in rows
                if r.get("class_event_id") is not None
                and (
                    r.get("adb_taxon_id") is not None
                    or r.get("adb_latin_name_id") is not None
                    or r.get("latin_name")
                    or r.get("valid_classterm")
                    or r.get("classterm")
                )
            ]
            stats.determination_created += max(1, len(det_rows))
            return

        ce = Collectingevent(**ce_kwargs)
        ce.save()
        stats.ce_created += 1

        # 3. Collector links (all resolved agents on the collecting event)
        collectors_created = 0
        if collecting_event_id is not None:
            collectors_created = _attach_collectors_to_collecting_event(
                owner=owner,
                collecting_event_id=int(collecting_event_id),
                ce=ce,
                oracle_cursor=oracle_cursor,
                object_id=object_id,
                stats=stats,
            )
        if collectors_created == 0 and collector_str:
            fallback_remarks = ce_kwargs.get("remarks") or ""
            collector_remark = f"Collector: {collector_str}"
            ce.remarks = _trunc(
                "; ".join(p for p in (fallback_remarks, collector_remark) if p),
                4000,
            )
            ce.save(update_fields=["remarks"])

        # 4. CollectionObject
        # Build remarks from notes and audit info
        co_remarks_parts = []
        object_notes = obj_row.get("object_notes")
        if object_notes:
            co_remarks_parts.append(f"Notes: {object_notes}")

        audit_parts = []
        if obj_row.get("reg_user"):
            audit_parts.append(f"Reg: {obj_row.get('reg_user')}")
        if obj_row.get("korr_user"):
            audit_parts.append(f"Korr: {obj_row.get('korr_user')}")
        if obj_row.get("approve_user"):
            audit_parts.append(f"Approve: {obj_row.get('approve_user')}")
        if audit_parts:
            co_remarks_parts.append(f"Audit: {', '.join(audit_parts)}")

        if obj_row.get("uuid"):
            co_remarks_parts.append(f"MUSIT UUID: {obj_row.get('uuid')}")

        # Administrative / audit block (MUSIT "Adm" tab) → readonly text8.
        admin_bits: list[str] = []
        reg_user = obj_row.get("reg_user")
        reg_date_txt = _short_date(obj_row.get("reg_date"))
        if reg_user or reg_date_txt:
            admin_bits.append(f"Reg: {' '.join(p for p in (reg_user, reg_date_txt) if p)}")
        korr_user = obj_row.get("korr_user")
        korr_date_txt = _short_date(obj_row.get("korr_date"))
        if korr_user or korr_date_txt:
            admin_bits.append(f"Corr: {' '.join(p for p in (korr_user, korr_date_txt) if p)}")
        approve_user = obj_row.get("approve_user")
        approved_date_txt = _short_date(obj_row.get("approved_date"))
        if approve_user or approved_date_txt:
            admin_bits.append(f"Approved: {' '.join(p for p in (approve_user, approved_date_txt) if p)}")
        modified_by = obj_row.get("last_modified_by")
        modified_date_txt = _short_date(obj_row.get("last_modified"))
        if modified_by or modified_date_txt:
            admin_bits.append(f"Modified: {' '.join(p for p in (modified_by, modified_date_txt) if p)}")
        admin_text = _trunc("; ".join(admin_bits), 4000) if admin_bits else None

        co = Collectionobject(
            catalognumber=_trunc(obj_row.get("identifier_string"), 32),
            guid=f"urn:oracle:{owner.lower()}:object:{object_id}"[:128],
            collectingevent=ce,
            collection=collection,
            collectionmemberid=int(collection.id),
            remarks=_trunc("; ".join(co_remarks_parts), 4000) if co_remarks_parts else None,
            text1=_trunc(obj_row.get("object_state"), 255),  # Object origin/state
            text2=_trunc(obj_row.get("project_name"), 255),  # Project
            text3=json_payload,
            text4=_trunc(obj_row.get("ex_herb"), 255),       # Ex Herbarium
            text5=_trunc(obj_row.get("voucher"), 255),       # Voucher
            text6=_trunc(obj_row.get("same_sheet_as"), 255), # Same sheet as
            text7=_trunc(obj_row.get("analysis_request"), 255), # Analysis request
            text8=admin_text,                                # Administrative (MUSIT) audit
            fieldnumber=_trunc(obj_row.get("identifier_num"), 50),
            altcatalognumber=_trunc(obj_row.get("artsobs_nr"), 32),
            countamt=obj_row.get("number_of_sheets"),
        )

        # MUSIT registration workflow flags → yesNo1/2/3/4 (Registered / Corrected / Approved / Withheld).
        co.yesno1 = _musit_bool(obj_row.get("is_reg"))
        co.yesno2 = _musit_bool(obj_row.get("is_corrected"))
        co.yesno3 = _musit_bool(obj_row.get("is_approved"))
        co.yesno4 = _musit_bool(obj_row.get("object_withheld"))

        # Registration date → catalogedDate; approval date → date1.
        reg_date = _coerce_date(obj_row.get("reg_date"))
        if reg_date is not None:
            co.catalogeddate = reg_date
            co.catalogeddateprecision = 1
        approved_date = _coerce_date(obj_row.get("approved_date"))
        if approved_date is not None:
            co.date1 = approved_date
            co.date1precision = 1

        co.save()
        stats.co_created += 1

        # 4b. URL-only image attachment from USD_FELLES (one Unimus link per media group).
        mgid_raw = obj_row.get("mediagruppe_enhets_id")
        if mgid_raw is not None:
            try:
                _attach_media_urls_to_collection_object(
                    oracle_cursor=oracle_cursor,
                    co=co,
                    media_group_id=int(mgid_raw),
                )
            except Exception as exc:  # noqa: BLE001
                _log("warning", "object_id=%s: media URL attachment migration failed: %s", object_id, exc)

        # 5. Determination(s) — deduplicate by best available source keys/text.
        seen_det_keys: set[tuple] = set()
        taxontreedef_id = int(discipline.taxontreedef_id)
        latin_name_lineage_cache: dict[int, dict[str, Any]] = {}
        rank_item_cache: dict[int, dict[str, Any]] = {}

        # Sort rows so that the "most current" determination (lowest class_event_id = oldest,
        # highest = most recent — we treat highest event_id as current).
        det_rows_all = [
            r
            for r in rows
            if r.get("class_event_id") is not None
            and (
                r.get("latin_name_id") is not None
                or r.get("adb_taxon_id") is not None
                or r.get("adb_latin_name_id") is not None
                or r.get("nhm_taxon_id") is not None
                or r.get("valid_classterm")
                or r.get("classterm")
            )
        ]
        if not det_rows_all:
            # No determination data; create a blank determination so the CO is valid.
            Determination.objects.create(
                collectionobject=co,
                iscurrent=True,
            )
            stats.determination_created += 1
        else:
            # Sort by class_event_id descending so the first we process is the most recent.
            def _det_sort_key(r: dict) -> int:
                v = r.get("class_event_id") or r.get("event_id") or 0
                try:
                    return -int(v)
                except (TypeError, ValueError):
                    return 0

            det_rows_sorted = sorted(det_rows_all, key=_det_sort_key)
            has_current = False

            for idx, dr in enumerate(det_rows_sorted):
                adb_taxon_id = dr.get("adb_taxon_id")
                adb_id = dr.get("adb_latin_name_id")
                ln_id = dr.get("latin_name_id")
                det_key = _determination_dedupe_key(dr)
                if det_key in seen_det_keys:
                    continue
                seen_det_keys.add(det_key)

                taxon, created_nodes = _resolve_or_create_taxon(
                    oracle_cursor=oracle_cursor,
                    owner=owner,
                    latin_name_id=ln_id,
                    adb_taxon_id=adb_taxon_id,
                    adb_latin_name_id=adb_id,
                    nhm_taxon_id=dr.get("nhm_taxon_id"),
                    latin_name=dr.get("latin_name") or dr.get("valid_classterm") or dr.get("classterm"),
                    valid_classterm=dr.get("valid_classterm"),
                    full_name=dr.get("full_name"),
                    full_name_author=dr.get("full_name_author"),
                    taxontreedef_id=taxontreedef_id,
                    lineage_cache=latin_name_lineage_cache,
                    rank_item_cache=rank_item_cache,
                    object_id=object_id,
                    catalog_number=_trunc(obj_row.get("identifier_string"), 32),
                )
                if created_nodes:
                    _log("debug", "object_id=%s catalog=%s fallback_debug=%s", object_id, obj_row.get("identifier_string"), created_nodes[0])
                if taxon is not None:
                    stats.taxon_matched += 1
                    if adb_taxon_id is None:
                        stats.taxon_fallback_matched += 1
                else:
                    stats.taxon_unresolved += 1
                    fallback_debug = created_nodes[0] if created_nodes else {}
                    candidate_scores = fallback_debug.get("fallback_candidates", [])
                    _log(
                        "debug",
                        "object_id=%s catalog=%s unresolved taxon det_class_term_id=%r det_latin_name_id=%r det_latin_name=%r probe=%r top_candidates=%s adb_taxon_id=%r adb_latin_name_id=%r nhm_taxon_id=%r",
                        object_id,
                        obj_row.get("identifier_string"),
                        dr.get("class_term_id"),
                        dr.get("latin_name_id"),
                        dr.get("latin_name") or dr.get("valid_classterm") or dr.get("classterm"),
                        fallback_debug.get("fallback_probe"),
                        candidate_scores[:5],
                        adb_taxon_id,
                        adb_id,
                        dr.get("nhm_taxon_id"),
                    )
                    if len(stats.errors) < _MAX_ERRORS:
                        stats.errors.append(
                            f"object_id={object_id}: unresolved taxon "
                            f"(class_term_id={dr.get('class_term_id')!r}, "
                            f"latin_name_id={dr.get('latin_name_id')!r}, "
                            f"latin_name={dr.get('latin_name')!r}, "
                            f"valid_classterm={dr.get('valid_classterm')!r}, "
                            f"classterm={dr.get('classterm')!r}, "
                            f"probe={fallback_debug.get('fallback_probe')!r}, "
                            f"top_candidates={candidate_scores[:5]!r}, "
                            f"adb_taxon_id={adb_taxon_id!r}, "
                            f"adb_latin_name_id={adb_id!r}, nhm_taxon_id={dr.get('nhm_taxon_id')!r})"
                        )

                # Determiner: MUSIT roles on the classification (determination) event — not the collecting event.
                det_actor = _first_non_null_classification_determiner_actor_id(
                    det_rows_all, det_key
                )
                determiner = _resolve_agent(owner, det_actor) if det_actor else None
                det_date = _coerce_date(dr.get("class_from_date")) or _coerce_date(
                    dr.get("class_to_date")
                )
                det_datetime = _coerce_datetime_with_optional_time(
                    det_date, dr.get("class_time_as_text")
                )

                is_current = not has_current
                # Infraspecific rank + name (MUSIT CLASSIFICATION_TERM) → text3.
                infraspes_parts = [
                    p
                    for p in (
                        _trunc(dr.get("infraspes_rank_name"), 64),
                        _trunc(dr.get("infraspes_name"), 128),
                    )
                    if p
                ]
                infraspes_text = " ".join(infraspes_parts) if infraspes_parts else None
                # MUSIT object-level type status applies to the current determination only.
                type_status = _trunc(obj_row.get("type_status"), 50) if is_current else None

                det = Determination(
                    collectionobject=co,
                    taxon=taxon,
                    iscurrent=is_current,
                    typestatusname=type_status,
                    text1=_trunc(dr.get("classterm"), 255),
                    text2=_trunc(dr.get("valid_classterm"), 255),
                    text3=infraspes_text,
                    text4=_trunc(dr.get("sensu_term"), 128),  # Sensu
                    determiner=determiner,
                    determineddate=det_datetime,
                    determineddateprecision=(1 if det_datetime is not None else None),
                )
                if not determiner:
                    det_verbatim = dr.get("det_agg_personnames") or dr.get("detname_orig")
                    if det_verbatim:
                        det.remarks = _trunc(f"Determiner (verbatim): {det_verbatim}", 4000)
                det.save()
                if det.iscurrent:
                    has_current = True
                stats.determination_created += 1

        # 6. Upsert objectmap row
        upsert_objectmap_row(
            source_owner=owner.upper(),
            source_id=str(object_id),
            specify_co_id=int(co.id),
            specify_collection_id=int(collection.id),
            run_ts=run_ts,
            dry_run=False,
        )


# ---------------------------------------------------------------------------
# Oracle fetch helpers
# ---------------------------------------------------------------------------


def _fetch_page_object_ids(
    oracle_cursor: Any,
    config: MusitDatasetConfig,
    skip: int,
    batch: int,
) -> list[int]:
    sql = _PAGE_SQL.format(schema=config.oracle_schema)
    oracle_cursor.execute(
        sql,
        {"icode": config.institutioncode, "ccode": config.collectioncode, "skip": skip, "batch": batch},
    )
    return [int(row[0]) for row in oracle_cursor.fetchall()]


def _fetch_specimen_rows(
    oracle_cursor: Any,
    config: MusitDatasetConfig,
    object_ids: list[int],
) -> list[dict]:
    if not object_ids:
        return []
    placeholders = ", ".join([":oid" + str(i) for i in range(len(object_ids))])
    sql = _SPECIMEN_SQL.format(schema=config.oracle_schema, placeholders=placeholders)
    binds = {f"oid{i}": oid for i, oid in enumerate(object_ids)}
    oracle_cursor.execute(sql, binds)
    cols = [d[0].lower() for d in oracle_cursor.description]
    return [dict(zip(cols, row)) for row in oracle_cursor.fetchall()]


def _count_total_objects(oracle_cursor: Any, config: MusitDatasetConfig) -> int:
    sql = f"""
    SELECT COUNT(*)
      FROM {config.oracle_schema}.v_object_attributes
     WHERE institutioncode = :icode
       AND collectioncode  = :ccode
    """
    oracle_cursor.execute(sql, {"icode": config.institutioncode, "ccode": config.collectioncode})
    row = oracle_cursor.fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Context: resolve Specify Collection + Discipline
# ---------------------------------------------------------------------------


@dataclass
class _SpecifyContext:
    collection: Any
    discipline: Any


def _resolve_specify_context(config: MusitDatasetConfig) -> _SpecifyContext:
    from specifyweb.specify.models import Collection, Discipline

    collection = Collection.objects.filter(code__iexact=config.specify_collection_code).first()
    if collection is None:
        raise RuntimeError(
            f"Specify Collection with code={config.specify_collection_code!r} not found. "
            "Run sync_specify_structure_flow first."
        )
    discipline = Discipline.objects.filter(
        name__iexact=config.specify_discipline_name
    ).order_by("id").first()
    if discipline is None:
        raise RuntimeError(
            f"Specify Discipline with name={config.specify_discipline_name!r} not found."
        )
    return _SpecifyContext(collection=collection, discipline=discipline)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def load_musit_dataset(
    config: MusitDatasetConfig,
    *,
    oracle_cursor: Any,
    dry_run: bool,
    limit: int | None = None,
    page_size: int = 1000,
    run_ts: str,
) -> DatasetLoadStats:
    """Stream Oracle MUSIT objects into Specify for the given dataset config.

    Args:
        config:       Dataset identity (schema, filter codes, target collection).
        oracle_cursor: Open Oracle cursor (caller manages connection lifecycle).
        dry_run:      When True, resolve and count but do not write to Specify or bridge tables.
        limit:        Stop after this many objects (for test runs with time estimates).
        page_size:    Number of OBJECT_IDs per Oracle page.
        run_ts:       ISO timestamp string stamped on all bridge table rows.

    Returns:
        ``DatasetLoadStats`` with counters and (when ``limit`` is set) a time estimate.
    """
    from flows.lib.migration_oracle_objectmap import (
        ensure_objectmap_table,
        object_ids_already_migrated,
    )
    from flows.lib.migration_oracle_placemap import ensure_placemap_table

    stats = DatasetLoadStats()
    t0 = time.monotonic()

    ctx = _resolve_specify_context(config)
    collection = ctx.collection
    discipline = ctx.discipline

    _log("info", "load_musit_dataset | collection=%s discipline=%s dry_run=%s limit=%s",
         config.specify_collection_code, config.specify_discipline_name, dry_run, limit)

    ensure_objectmap_table(dry_run=dry_run)
    ensure_placemap_table(dry_run=dry_run)

    # Load already-migrated OBJECT_IDs for idempotency.
    already_done: set[int] = set()
    if not dry_run:
        already_done = object_ids_already_migrated(
            source_owner=config.oracle_schema.upper(),
            specify_collection_id=int(collection.id),
        )
        if already_done:
            _log("info", "load_musit_dataset | idempotency: %s objects already in objectmap → will skip",
                 len(already_done))

    total_oracle = _count_total_objects(oracle_cursor, config)
    _log("info", "load_musit_dataset | total Oracle objects for filter: %s", total_oracle)

    # In-memory locality cache: (discipline_id, place_id) → specify locality pk
    locality_cache: dict[tuple[int, int], int] = {}
    total_processed = 0
    skip = 0

    while True:
        # Periodic Django connection refresh (long-running flow protection).
        if total_processed > 0 and total_processed % 5000 == 0:
            close_old_connections()

        page_ids = _fetch_page_object_ids(oracle_cursor, config, skip=skip, batch=page_size)
        if not page_ids:
            break

        # Filter already-migrated.
        ids_to_process = [oid for oid in page_ids if oid not in already_done]

        # Fetch full specimen rows for this page (one round-trip).
        if ids_to_process:
            specimen_rows = _fetch_specimen_rows(oracle_cursor, config, ids_to_process)
            grouped = _group_rows_by_object_id(specimen_rows)
        else:
            grouped = {}

        for oid in page_ids:
            if oid in already_done:
                stats.co_skipped += 1
                continue

            rows = grouped.get(oid)
            if not rows:
                # Object visible in V_OBJECT_ATTRIBUTES but no rows in join — skip.
                _log("debug", "object_id=%s: no specimen rows returned; skipping", oid)
                continue

            _write_one_object(
                object_id=oid,
                rows=rows,
                config=config,
                collection=collection,
                discipline=discipline,
                oracle_cursor=oracle_cursor,
                run_ts=run_ts,
                dry_run=dry_run,
                locality_cache=locality_cache,
                stats=stats,
            )

            total_processed += 1

            if total_processed % _PROGRESS_EVERY == 0 or total_processed == 1:
                elapsed = time.monotonic() - t0
                pct = 100.0 * total_processed / total_oracle if total_oracle else 0.0
                _log(
                    "info",
                    "load_musit_dataset | %s/%s (%.1f%%) co=%s ce=%s loc_new=%s det=%s"
                    " taxon_ok=%s taxon_fallback=%s taxon_unresolved=%s agent_ok=%s err=%s elapsed=%s",
                    total_processed,
                    total_oracle,
                    pct,
                    stats.co_created,
                    stats.ce_created,
                    stats.locality_created,
                    stats.determination_created,
                    stats.taxon_matched,
                    stats.taxon_fallback_matched,
                    stats.taxon_unresolved,
                    stats.agent_matched,
                    len(stats.errors),
                    _format_duration(elapsed),
                )

            if limit is not None and total_processed >= limit:
                elapsed = time.monotonic() - t0
                rate = total_processed / elapsed if elapsed > 0 else 0.0
                estimate_s = (total_oracle / rate) if rate > 0 else None
                stats.estimate_total_s = estimate_s
                _log(
                    "info",
                    "load_musit_dataset | limit=%s reached after %s (%.2f obj/s) — "
                    "estimated full migration: %s",
                    limit,
                    _format_duration(elapsed),
                    rate,
                    _format_duration(estimate_s) if estimate_s else "unknown",
                )
                stats.elapsed_s = elapsed
                return stats

        skip += page_size
        if len(page_ids) < page_size:
            break  # last page

    stats.elapsed_s = time.monotonic() - t0
    _log(
        "info",
        "load_musit_dataset | done total_processed=%s co=%s ce=%s loc_new=%s det=%s"
        " taxon_ok=%s agent_ok=%s skipped=%s err=%s elapsed=%s",
        total_processed,
        stats.co_created,
        stats.ce_created,
        stats.locality_created,
        stats.determination_created,
        stats.taxon_matched,
        stats.agent_matched,
        stats.co_skipped,
        len(stats.errors),
        _format_duration(stats.elapsed_s),
    )
    return stats
