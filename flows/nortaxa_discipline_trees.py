"""Prefect flow: NorTaxa API extract + merge + changelog sync into Specify ``Taxon`` trees.

1. **Extract** — ``DataTransfer/Export`` per discipline root (``flows.lib.nortaxa_discipline_root_specs``).
2. **Merge** — bootstrap tree if needed, merge accepted + synonym taxa with correct epithet names.
3. **Changelog** — incremental sync from ``TaxonName/ChangeLog`` (auto-synonym; review queue for Merge/Split/Delete).

Use ``dry_run=True`` (default) for artifact-only runs; ``dry_run=False`` to persist to Specify.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_NORTAXA_DISCIPLINE_TREES,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.nortaxa_changelog_sync import (
    DEFAULT_WATERMARK_PATH,
    sync_nortaxa_changelog,
)
from flows.lib.nortaxa_discipline_root_specs import NorTaxaSliceSpec, plan_nortaxa_slice
from flows.lib.nortaxa_discipline_subtrees import (
    NORTAXA_API_BASE_URL,
    run_api_export_for_specify_disciplines,
)
from flows.lib.specify_setup import setup_django


@task(name="NorTaxa API discipline export")
def api_export_task(
    out_dir: Path,
    jobs: list[tuple[int, str | None, int, str | None, NorTaxaSliceSpec]],
    api_base_url: str,
) -> dict:
    return run_api_export_for_specify_disciplines(
        out_dir=out_dir,
        discipline_jobs=jobs,
        api_base_url=api_base_url,
    )


@task(name="NorTaxa changelog sync")
def changelog_sync_task(
    treedef_ids: list[int],
    *,
    from_date: str | None,
    watermark_path: str,
    dry_run: bool,
    api_base_url: str,
) -> dict:
    from prefect import get_run_logger

    return sync_nortaxa_changelog(
        treedef_ids=treedef_ids,
        from_date=from_date,
        watermark_path=Path(watermark_path),
        dry_run=dry_run,
        api_base_url=api_base_url,
        logger=get_run_logger(),
    )


def _discipline_slice_jobs_from_specify():
    """Build slice jobs from ``Discipline`` rows; skipped rows are returned separately."""
    from specifyweb.specify.models import Discipline

    jobs: list[tuple[int, str | None, int, str | None, NorTaxaSliceSpec]] = []
    skipped: list[dict] = []
    qs = Discipline.objects.select_related("division").order_by("division_id", "id")
    for d in qs:
        run, reason, spec = plan_nortaxa_slice(d.name)
        div = d.division
        divname = getattr(div, "name", None) if div is not None else None
        divid = int(div.id) if div is not None else 0
        if not run:
            skipped.append(
                {
                    "discipline_id": int(d.id),
                    "discipline_name": d.name,
                    "division_id": divid,
                    "division_name": divname,
                    "skip_reason": reason,
                }
            )
            continue
        jobs.append((int(d.id), d.name, divid, divname, spec))
    return jobs, skipped


@flow(
    name="NorTaxa discipline taxon trees",
    description=(
        "NorTaxa API export per discipline; merge into Specify taxon trees; "
        "optional changelog incremental sync."
    ),
)
def nortaxa_discipline_trees_flow(
    api_base_url: str = NORTAXA_API_BASE_URL,
    output_parent: str | None = None,
    dry_run: bool = True,
    run_changelog_sync: bool = True,
    changelog_from_date: str | None = None,
    changelog_watermark_path: str | None = None,
) -> dict:
    """Export NorTaxa taxa per discipline via API and merge into Specify."""
    logger = get_run_logger()
    setup_django()
    jobs, skipped = _discipline_slice_jobs_from_specify()
    logger.info(
        "Specify disciplines: %s slice job(s), %s skipped",
        len(jobs),
        len(skipped),
    )
    for s in skipped:
        logger.info(
            "Skip discipline id=%s name=%r division=%r reason=%s",
            s.get("discipline_id"),
            s.get("discipline_name"),
            s.get("division_name"),
            s.get("skip_reason"),
        )

    repo_root = Path(__file__).resolve().parent.parent
    out_parent = Path(output_parent).resolve() if output_parent else repo_root / "data"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_parent / "nortaxa-discipline-trees" / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting discipline taxa via NorTaxa API (%s)", api_base_url)
    if not jobs:
        logger.warning("No discipline slice jobs — check Specify Discipline names vs root_specs")
    manifest = api_export_task(run_dir, jobs, api_base_url)

    manifest["skipped_disciplines"] = skipped
    manifest["flow"] = "nortaxa_discipline_trees"
    manifest["timestamp_utc"] = ts
    manifest["api_base_url"] = api_base_url
    manifest["dry_run"] = dry_run
    manifest["output_dir"] = str(run_dir)

    export_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["export_stamp"] = export_stamp

    from specifyweb.specify.models import Discipline

    from flows.lib.nortaxa_specify_merge import (
        ensure_discipline_taxon_tree,
        merge_nortaxa_tsv_into_discipline_tree,
        merge_stats_to_dict,
    )

    merge_summaries: list[dict] = []
    trees_merged: set[int] = set()
    merged_treedef_ids: list[int] = []
    for a in manifest.get("artifacts", []):
        did = int(a["discipline_id"])
        disc = Discipline.objects.filter(id=did).first()
        if disc is None:
            merge_summaries.append({"discipline_id": did, "error": "discipline_not_found"})
            continue

        ens = ensure_discipline_taxon_tree(disc.id, dry_run=dry_run)
        if ens.get("error"):
            merge_summaries.append({"discipline_id": did, "error": ens["error"]})
            continue
        if ens.get("dry_run_blocked"):
            merge_summaries.append(
                {
                    "discipline_id": disc.id,
                    "discipline_name": disc.name,
                    "skip_reason": "dry_run_tree_bootstrap_needed",
                    "detail": ens.get("message"),
                    "tree_bootstrap": {k: ens[k] for k in ("created_treedef", "created_rank_item", "created_root") if k in ens},
                }
            )
            continue

        td_int = int(ens["treedef_id"])
        if td_int in trees_merged:
            merge_summaries.append(
                {
                    "discipline_id": disc.id,
                    "discipline_name": disc.name,
                    "treedef_id": td_int,
                    "skip_reason": "treedef_already_merged_this_run",
                }
            )
            continue
        trees_merged.add(td_int)
        merged_treedef_ids.append(td_int)

        tsv_path = run_dir / str(a["artifact"])
        st = merge_nortaxa_tsv_into_discipline_tree(
            discipline_id=int(disc.id),
            discipline_name=disc.name,
            treedef_id=td_int,
            tsv_path=tsv_path,
            export_stamp=export_stamp,
            dry_run=dry_run,
            logger=logger,
        )
        entry = merge_stats_to_dict(st)
        entry["tree_bootstrap"] = {
            k: ens[k]
            for k in ("created_treedef", "created_rank_item", "created_root", "treedef_id")
            if k in ens
        }
        merge_summaries.append(entry)
        logger.info(
            "Merge treedef=%s discipline=%r dry_run=%s inserted=%s synonyms_linked=%s "
            "orphans=%s skipped_unknown_rank=%s skipped_missing_parent=%s present_refreshed=%s",
            td_int,
            disc.name,
            dry_run,
            st.inserted,
            st.synonyms_linked,
            st.orphans_marked,
            st.skipped_unknown_rank,
            st.skipped_missing_parent,
            st.present_refreshed,
        )
        if st.errors:
            logger.warning("Merge treedef=%s errors: %s", td_int, "; ".join(st.errors))
    manifest["merge"] = merge_summaries

    changelog_summary: dict | None = None
    if run_changelog_sync and merged_treedef_ids:
        wm_path = changelog_watermark_path or str(DEFAULT_WATERMARK_PATH)
        changelog_summary = changelog_sync_task(
            merged_treedef_ids,
            from_date=changelog_from_date,
            watermark_path=wm_path,
            dry_run=dry_run,
            api_base_url=api_base_url,
        )
        manifest["changelog_sync"] = changelog_summary
        if changelog_summary.get("review_items"):
            logger.warning(
                "Changelog: %s event(s) queued for curator review (Merge/Split/Delete)",
                changelog_summary.get("review_queued", 0),
            )

    logger.info("Wrote %s taxon artifact(s) under %s", len(manifest.get("artifacts", [])), run_dir)
    for a in manifest.get("artifacts", []):
        logger.info(
            "  %s / %s: %s rows -> %s",
            a.get("division_name"),
            a.get("discipline_name"),
            a.get("rows"),
            a.get("artifact"),
        )

    s3_key = migration_report_s3_key(REPORT_CATEGORY_NORTAXA_DISCIPLINE_TREES, ts)
    uploaded = upload_migration_report_json_task(manifest, s3_key)
    if uploaded:
        for uri in uploaded:
            logger.info("Report: %s", uri)

    return {**manifest, "uploaded": uploaded, "report_uploaded": bool(uploaded)}
