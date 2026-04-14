"""Prefect flow: NorTaxa DwC-A extract + merge into Specify ``Taxon`` trees.

1. **Extract** — download/unpack IPT archive, slice ``taxon.txt`` / ``vernacularname.txt`` per
   discipline (``flows.lib.nortaxa_discipline_root_specs``).
2. **Merge** — always runs after extract: ensures each discipline has a ``TaxonTreeDef`` + root
   ``Life`` when missing (unless ``dry_run``), then merges the slice (``flows.lib.nortaxa_specify_merge``).

Use ``dry_run=True`` (default) to write only TSV artifacts and **not** change Specify; use
``dry_run=False`` to persist tree bootstrap and taxon rows (same pattern as other migration flows).
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_NORTAXA_DISCIPLINE_TREES,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.nortaxa_discipline_root_specs import NorTaxaSliceSpec, plan_nortaxa_slice
from flows.lib.nortaxa_discipline_subtrees import (
    ARTSNAVNEBASE_ARCHIVE_URL,
    download_archive,
    extract_dwca_zip,
    run_slice_for_specify_disciplines,
)
from flows.lib.specify_setup import setup_django


@task(name="Download NorTaxa DwC-A")
def download_nortaxa_task(url: str, dest_zip: Path) -> Path:
    return download_archive(url, dest_zip)


@task(name="Unpack DwC-A zip")
def unpack_dwca_task(zip_path: Path, dest_dir: Path) -> str:
    root = extract_dwca_zip(zip_path, dest_dir)
    return str(root)


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
        "Download Artsnavnebase DwC-A; slice taxon/vernacular per discipline; merge into Specify "
        "(single dry_run flag; creates taxon tree + root when missing)."
    ),
)
def nortaxa_discipline_trees_flow(
    archive_url: str = ARTSNAVNEBASE_ARCHIVE_URL,
    download: bool = True,
    dwca_dir: str | None = None,
    output_parent: str | None = None,
    keep_unpack_dir: bool = False,
    dry_run: bool = True,
) -> dict:
    """Download (optional), unpack, slice, then merge NorTaxa into Specify per discipline.

    Args:
        archive_url: IPT ``archive.do`` URL (default Artsnavnebase).
        download: When True, fetch ``archive_url`` then unpack. When False, ``dwca_dir`` must
            point at an unpacked DwC-A directory (containing ``taxon.txt``).
        dwca_dir: Unpacked DwC-A root; required when ``download`` is False.
        output_parent: Directory for ``nortaxa-discipline-trees/{timestamp}/``. Defaults to
            repo ``data/``.
        keep_unpack_dir: When True and ``download`` is True, copy unpacked DwC-A into the run
            directory as ``dwca-unpacked/`` (otherwise the temp unpack dir is removed).
        dry_run: When True (default), write TSV artifacts only; merge phase counts/skips DB writes
            and does **not** bootstrap a missing taxon tree. When False, create ``TaxonTreeDef`` /
            root ``Life`` if needed and merge taxa into Specify.
    """
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

    unpacked: Path | None = None
    temp_unpack: Path | None = None
    try:
        if download:
            zip_path = Path(tempfile.mkdtemp(prefix="nortaxa-zip-")) / "artsnavnebase.zip"
            download_nortaxa_task(archive_url, zip_path)
            temp_unpack = Path(tempfile.mkdtemp(prefix="nortaxa-dwca-"))
            dwca_str = unpack_dwca_task(zip_path, temp_unpack)
            unpacked = Path(dwca_str)
            shutil.rmtree(zip_path.parent, ignore_errors=True)
            if keep_unpack_dir:
                preserved = run_dir / "dwca-unpacked"
                shutil.copytree(unpacked, preserved, dirs_exist_ok=True)
                logger.info("Copied unpacked DwC-A to %s", preserved)
        else:
            if not dwca_dir:
                raise ValueError("dwca_dir is required when download=False")
            unpacked = Path(dwca_dir).resolve()
            if not (unpacked / "taxon.txt").is_file():
                raise FileNotFoundError(f"taxon.txt not found under {unpacked}")

        assert unpacked is not None
        if not jobs:
            logger.warning("No discipline slice jobs — check Specify Discipline names vs root_specs")
        manifest = run_slice_for_specify_disciplines(
            dwca_dir=unpacked,
            out_dir=run_dir,
            discipline_jobs=jobs,
        )
    finally:
        if temp_unpack is not None and temp_unpack.exists() and not keep_unpack_dir:
            shutil.rmtree(temp_unpack, ignore_errors=True)

    manifest["skipped_disciplines"] = skipped
    manifest["flow"] = "nortaxa_discipline_trees"
    manifest["timestamp_utc"] = ts
    manifest["download"] = download
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
            "Merge treedef=%s discipline=%r dry_run=%s inserted=%s orphans_marked=%s",
            td_int,
            disc.name,
            dry_run,
            st.inserted,
            st.orphans_marked,
        )
    manifest["merge"] = merge_summaries

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
