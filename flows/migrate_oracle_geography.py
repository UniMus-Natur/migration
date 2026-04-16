"""Prefect flow: Oracle MUSIT geolocation → shared Specify Geography tree + Locality + placemap.

- Links all biology disciplines to one ``GeographyTreeDef`` (canonical: Karplanter Moser).
- Loads ``HIERARCHICAL_PLACE_OLD`` into ``Geography`` (Django ORM).
- Creates ``Locality`` rows per referenced ``PLACE_ID`` for each linked discipline.
- Persists ``migration_oracle_placemap`` in Specify MariaDB for specimen migration joins.

Parameters mirror other migration flows: ``oracle_env``, ``dry_run``, ``musit_schemas``,
``max_places`` (optional cap for Locality pass).

Optional **purge** (single treedef): removes all ``Geography`` rows for the canonical treedef only,
nulls ``Locality.GeographyID`` for those nodes, deletes blocking ``Agentgeography`` rows, recreates
**Earth** for that treedef, and (by default) **TRUNCATE** ``migration_oracle_placemap``.

Optional **purge all**: ``purge_all_geography_trees_before_oracle_import`` wipes **every**
``Geography`` row in the database (all treedefs), nulls **all** locality geography links, then
recreates an **Earth** root per ``GeographyTreeDef`` that has rank items. Use only on databases
where destroying every geography tree is acceptable (e.g. migration staging). When both purge
flags are true, the global purge runs and the single-treedef purge is skipped.

It does **not** delete ``Locality`` rows (specimens may still reference them).
"""

from __future__ import annotations

from datetime import datetime, timezone

from prefect import flow, get_run_logger, task

from flows.lib.migration_oracle_placemap import ensure_placemap_table
from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.oracle_connectivity import create_oracle_connection, get_oracle_config_from_env
from flows.lib.oracle_geography_inventory import (
    inventory_musit_botany,
    inventory_musit_entomology,
    inventory_usd_sample,
    referenced_place_ids_count,
)
from flows.lib.oracle_geography_load import (
    OracleGeographyMigrationError,
    biology_discipline_ids_for_shared_treedef,
    load_hierarchical_geography,
    load_localities_for_referenced_places,
)
from flows.lib.specify_geography_purge import (
    purge_all_geography_trees,
    purge_geography_tree_for_treedef,
)
from flows.lib.specify_geography_shared import link_biology_disciplines_shared_geography
from flows.lib.specify_setup import setup_django


_DEFAULT_SCHEMAS = ("MUSIT_BOTANIKK_FELLES", "MUSIT_ZOOLOGI_ENTOMOLOGI")


@task(name="Oracle geography inventory")
def oracle_geography_inventory_task(oracle_env: str) -> dict:
    cfg = get_oracle_config_from_env(oracle_env)
    con = create_oracle_connection(cfg)
    try:
        cur = con.cursor()
        out: dict = {"musit_botanikk_felles": inventory_musit_botany(cur)}
        try:
            out["musit_zoologi_entomologi"] = inventory_musit_entomology(cur)
        except Exception as exc:  # noqa: BLE001
            out["musit_zoologi_entomologi"] = {"error": str(exc)[:500]}
        for usd in ("USD_BOTANIKK_TRONDHEIM", "USD_BOTANIKK_TROMSO", "USD_BOTANIKK_BERGEN", "USD_BOTANIKK_SVALBARD"):
            try:
                out[usd.lower()] = inventory_usd_sample(cur, usd)
                out[usd.lower()]["referenced_places"] = referenced_place_ids_count(cur, usd)
            except Exception as exc:  # noqa: BLE001
                out[usd.lower()] = {"error": str(exc)[:500]}
        return out
    finally:
        con.close()


@flow(
    name="Migrate Oracle geography to Specify",
    description=(
        "Shared GeographyTreeDef for biology; import HIERARCHICAL_PLACE_OLD; "
        "Locality per referenced PLACE; migration_oracle_placemap; S3 report."
    ),
)
def migrate_oracle_geography_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    musit_schemas: tuple[str, ...] = _DEFAULT_SCHEMAS,
    canonical_discipline_name: str = "Karplanter Moser",
    max_places: int | None = None,
    purge_all_geography_trees_before_oracle_import: bool = False,
    purge_existing_geography_for_treedef: bool = False,
    truncate_placemap_when_purging_geography: bool = True,
) -> dict:
    """Run geography linking, optional placemap DDL, Oracle → Specify load, upload JSON report."""
    logger = get_run_logger()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    setup_django()

    manifest: dict = {
        "flow": "migrate_oracle_geography",
        "timestamp_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "musit_schemas": list(musit_schemas),
        "canonical_discipline_name": canonical_discipline_name,
        "max_places": max_places,
        "purge_all_geography_trees_before_oracle_import": purge_all_geography_trees_before_oracle_import,
        "purge_existing_geography_for_treedef": purge_existing_geography_for_treedef,
        "truncate_placemap_when_purging_geography": truncate_placemap_when_purging_geography,
    }
    logger.info(
        "oracle_geography | flow start | ts=%s oracle_env=%s dry_run=%s schemas=%s canonical_discipline=%s "
        "max_places=%s purge_all_geography=%s purge_geography_treedef=%s truncate_placemap_on_purge=%s",
        ts,
        oracle_env,
        dry_run,
        list(musit_schemas),
        canonical_discipline_name,
        max_places,
        purge_all_geography_trees_before_oracle_import,
        purge_existing_geography_for_treedef,
        truncate_placemap_when_purging_geography,
    )

    inv = oracle_geography_inventory_task(oracle_env)
    manifest["inventory"] = inv
    logger.info("oracle_geography | inventory task completed")

    link_res = link_biology_disciplines_shared_geography(
        canonical_discipline_name=canonical_discipline_name,
        dry_run=dry_run,
    )
    manifest["shared_geography_treedef"] = link_res
    logger.info(
        "oracle_geography | shared treedef | canonical_treedef_id=%s updated_disciplines=%s",
        link_res.get("canonical_treedef_id"),
        len(link_res.get("updated_disciplines") or []),
    )
    if link_res.get("error"):
        logger.error("shared_geography_treedef: %s", link_res["error"])
        manifest["errors"] = [link_res["error"]]
        s3_key = migration_report_s3_key(REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY, ts)
        manifest["uploaded"] = upload_migration_report_json_task(manifest, s3_key)
        return manifest

    treedef_id = int(link_res["canonical_treedef_id"])

    placemap_meta = ensure_placemap_table(dry_run=dry_run)
    manifest["placemap_table"] = placemap_meta
    logger.info("oracle_geography | placemap table | %s", placemap_meta)

    if purge_all_geography_trees_before_oracle_import:
        if purge_existing_geography_for_treedef:
            logger.info(
                "oracle_geography | purge_all_geography_trees_before_oracle_import=True "
                "(single-treedef purge flag ignored)"
            )
        logger.warning(
            "oracle_geography | PURGE ALL Geography trees (every treedef) dry_run=%s truncate_placemap=%s",
            dry_run,
            truncate_placemap_when_purging_geography,
        )
        purge_all_meta = purge_all_geography_trees(
            dry_run=dry_run,
            truncate_migration_placemap=truncate_placemap_when_purging_geography,
        )
        manifest["geography_purge_all"] = purge_all_meta
    elif purge_existing_geography_for_treedef:
        logger.warning(
            "oracle_geography | PURGE GeographyTreeDefID=%s dry_run=%s truncate_placemap=%s",
            treedef_id,
            dry_run,
            truncate_placemap_when_purging_geography,
        )
        purge_meta = purge_geography_tree_for_treedef(
            treedef_id,
            dry_run=dry_run,
            truncate_migration_placemap=truncate_placemap_when_purging_geography,
        )
        manifest["geography_purge"] = purge_meta
        if purge_meta.get("error"):
            logger.error("geography_purge failed: %s", purge_meta["error"])
            manifest.setdefault("errors", []).append(purge_meta["error"])
            s3_key = migration_report_s3_key(REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY, ts)
            manifest["uploaded"] = upload_migration_report_json_task(manifest, s3_key)
            return manifest

    cfg = get_oracle_config_from_env(oracle_env)
    con = create_oracle_connection(cfg)
    geo_runs: list[dict] = []
    loc_runs: list[dict] = []
    discipline_ids = biology_discipline_ids_for_shared_treedef(treedef_id)
    manifest["discipline_ids_for_locality"] = discipline_ids
    run_ts = ts
    s3_key = migration_report_s3_key(REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY, ts)

    try:
        ocur = con.cursor()
        for owner in musit_schemas:
            o = owner.strip().upper()
            if o not in ("MUSIT_BOTANIKK_FELLES", "MUSIT_ZOOLOGI_ENTOMOLOGI"):
                continue
            logger.info(
                "oracle_geography | schema=%s | phase=geography (HIERARCHICAL_PLACE_OLD → Specify Geography)",
                o,
            )
            gstats, hid_map = load_hierarchical_geography(
                oracle_cursor=ocur,
                owner=o,
                treedef_id=treedef_id,
                dry_run=dry_run,
            )
            geo_runs.append(
                {
                    "owner": o,
                    "rows_read": gstats.rows_read,
                    "geographies_created": gstats.geographies_created,
                    "geographies_skipped_existing": gstats.geographies_skipped_existing,
                    "errors": gstats.errors[:80],
                }
            )
            logger.info(
                "oracle_geography | schema=%s | geography phase done | hid_map_size=%s rows_read=%s",
                o,
                len(hid_map),
                gstats.rows_read,
            )
            if not dry_run:
                logger.info(
                    "oracle_geography | schema=%s | phase=locality (referenced PLACE → Locality + placemap)",
                    o,
                )
                lstats = load_localities_for_referenced_places(
                    oracle_cursor=ocur,
                    owner=o,
                    oracle_hid_to_specify_geo=hid_map,
                    discipline_ids=discipline_ids,
                    treedef_id=treedef_id,
                    run_ts=run_ts,
                    dry_run=False,
                    max_places=max_places,
                )
                loc_runs.append(
                    {
                        "owner": o,
                        "places_seen": lstats.places_seen,
                        "localities_created": lstats.localities_created,
                        "localities_skipped": lstats.localities_skipped,
                        "errors": lstats.errors[:80],
                    }
                )
                logger.info(
                    "oracle_geography | schema=%s | locality phase done | places_seen=%s localities_created=%s",
                    o,
                    lstats.places_seen,
                    lstats.localities_created,
                )
            else:
                loc_runs.append(
                    {
                        "owner": o,
                        "skipped": True,
                        "reason": "dry_run skips Locality and placemap inserts",
                    }
                )
    except Exception as exc:
        manifest["fatal_error"] = repr(exc)
        if isinstance(exc, OracleGeographyMigrationError):
            manifest["fatal_context"] = exc.context
        manifest["geography_load"] = geo_runs
        manifest["locality_load"] = loc_runs
        try:
            manifest["uploaded_failure_report"] = upload_migration_report_json_task(manifest, s3_key)
        except Exception:
            logger.exception("oracle_geography | failure report upload failed")
        raise
    finally:
        con.close()

    manifest["geography_load"] = geo_runs
    manifest["locality_load"] = loc_runs
    logger.info("oracle_geography | Oracle connection closed; uploading report to S3")
    uploaded = upload_migration_report_json_task(manifest, s3_key)
    manifest["uploaded"] = uploaded
    manifest["report_uploaded"] = bool(uploaded)
    logger.info("migrate_oracle_geography finished dry_run=%s treedef_id=%s", dry_run, treedef_id)
    return manifest
