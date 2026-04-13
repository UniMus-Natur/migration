"""Reconcile YAML structure with Specify database (create-if-missing).

Django / ``specifyweb`` are imported only inside ``reconcile_structure`` so that
Prefect workers can import this module (and the flow module) before
``setup_django()`` adds the ``specify7`` directory to ``sys.path``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from flows.lib.specify_structure.config import DisciplineSpec, DivisionSpec, StructureConfig
from flows.lib.specify_structure.matching import norm_code, norm_name

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    divisions_created: int = 0
    divisions_skipped: int = 0
    disciplines_created: int = 0
    disciplines_skipped: int = 0
    collections_created: int = 0
    collections_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _build_discipline_trees_payload(
    division_id: int,
    spec: DisciplineSpec,
) -> dict:
    """Payload for ``create_discipline_and_trees_task`` (keys lowercased like HTTP)."""
    from specifyweb.backend.setup_tool.utils import normalize_keys

    discipline = {
        "name": spec.name,
        "type": spec.type,
        "division_id": division_id,
    }
    geographytreedef = {
        "name": spec.geography_tree.name,
        "preload": spec.geography_tree.preload,
    }
    if spec.geography_tree.preloadfile:
        geographytreedef["preloadfile"] = spec.geography_tree.preloadfile
    taxontreedef = {
        "name": spec.taxon_tree.name,
        "preload": spec.taxon_tree.preload,
    }
    if spec.taxon_tree.preloadfile:
        taxontreedef["preloadfile"] = spec.taxon_tree.preloadfile
    return normalize_keys(
        {
            "discipline": discipline,
            "geographytreedef": geographytreedef,
            "taxontreedef": taxontreedef,
        }
    )


def reconcile_structure(config: StructureConfig, *, dry_run: bool = True) -> ReconcileResult:
    """Apply structure: create missing divisions, disciplines (with trees), and collections.

    Idempotent: safe to re-run; existing rows are skipped. Discipline names are matched
    globally (Specify constraint). Collections matched by (discipline_id, code).

    Callers must invoke ``setup_django()`` from ``flows.lib.specify_setup`` first so
    ``specifyweb`` is importable.
    """
    from specifyweb.backend.setup_tool import api as setup_api
    from specifyweb.backend.setup_tool.setup_tasks import create_discipline_and_trees_task
    from specifyweb.backend.setup_tool.utils import normalize_keys
    from specifyweb.specify.api.serializers import uri_for_model
    from specifyweb.specify.models import Collection, Discipline, Division, Institution

    def _institution_by_name(name: str) -> Institution:
        inst = (
            Institution.objects.filter(name__iexact=norm_name(name))
            .order_by("id")
            .first()
        )
        if inst is None:
            raise LookupError(
                f"No institution found with name matching {name!r} (post-bootstrap required)."
            )
        dup = Institution.objects.exclude(id=inst.id).filter(name__iexact=norm_name(name)).exists()
        if dup:
            raise LookupError(
                f"Multiple institutions match name {name!r}; set a unique institution_name in YAML."
            )
        return inst

    def _find_division(institution_id: int, division_name: str) -> Division | None:
        return (
            Division.objects.filter(
                institution_id=institution_id,
                name__iexact=norm_name(division_name),
            )
            .order_by("id")
            .first()
        )

    def _find_discipline_by_global_name(discipline_name: str) -> Discipline | None:
        return (
            Discipline.objects.filter(name__iexact=norm_name(discipline_name))
            .select_related("division")
            .order_by("id")
            .first()
        )

    def _find_collection(discipline_id: int, code: str) -> Collection | None:
        c = norm_code(code)
        if not c:
            return None
        return (
            Collection.objects.filter(discipline_id=discipline_id, code__iexact=c)
            .order_by("id")
            .first()
        )

    def _create_division_row(
        institution: Institution, div_spec: DivisionSpec, result: ReconcileResult
    ) -> Division | None:
        payload = normalize_keys(
            {
                "name": div_spec.name,
                "institution": uri_for_model(Institution, institution.id),
            }
        )
        try:
            out = setup_api.create_division(payload)
        except setup_api.SetupError as e:
            result.errors.append(f"division {div_spec.name!r}: {e}")
            return None
        did = out.get("division_id")
        div = Division.objects.filter(id=did).first()
        if div is None:
            result.errors.append(f"division {div_spec.name!r}: create_division returned no row")
            return None
        result.divisions_created += 1
        logger.info("Created division %s id=%s", div_spec.name, div.id)
        return div

    def _create_discipline_row(
        division: Division,
        spec: DisciplineSpec,
        result: ReconcileResult,
    ) -> Discipline | None:
        payload = _build_discipline_trees_payload(division.id, spec)
        try:
            create_discipline_and_trees_task(payload)
        except Exception as e:
            result.errors.append(f"discipline {spec.name!r}: {e}")
            return None
        created = _find_discipline_by_global_name(spec.name)
        if created is None:
            result.errors.append(
                f"discipline {spec.name!r}: not found after create_discipline_and_trees_task"
            )
            return None
        result.disciplines_created += 1
        logger.info("Created discipline %s id=%s", spec.name, created.id)
        return created

    def _ensure_collection(
        discipline: Discipline,
        code: str,
        display_name: str,
        catalognumformatname: str | None,
        *,
        dry_run_inner: bool,
        result_inner: ReconcileResult,
    ) -> None:
        if not discipline.taxontreedef_id:
            result_inner.errors.append(
                f"collection {code!r}: discipline {discipline.name!r} has no taxontreedef_id; "
                "cannot create collection until a taxon tree exists."
            )
            return
        existing = _find_collection(discipline.id, code)
        if existing is not None:
            result_inner.collections_skipped += 1
            logger.info("Collection exists: %s (id=%s)", code, existing.id)
            return
        if dry_run_inner:
            result_inner.collections_created += 1
            logger.info("Would create collection: %s", code)
            return
        fmt = (catalognumformatname or code or "CatalogNumber").strip() or "CatalogNumber"
        if len(fmt) > 64:
            fmt = fmt[:64]
        payload = normalize_keys(
            {
                "discipline_id": discipline.id,
                "code": norm_code(code),
                "collectionname": norm_name(display_name) or norm_code(code),
                # Field name on Collection model is catalognumformatname (no "ber").
                "catalognumformatname": fmt,
                "isembeddedcollectingevent": False,
            }
        )
        try:
            # run_fix_schema_config_async=False: run synchronously here rather than
            # queuing a Celery task (same approach as setup_database_task).
            setup_api.create_collection(payload, run_fix_schema_config_async=False)
        except setup_api.SetupError as e:
            result_inner.errors.append(
                f"collection {code!r} (discipline {discipline.name!r}): {e}"
            )
            return
        result_inner.collections_created += 1
        logger.info("Created collection %s for discipline %s", code, discipline.name)

    def _dry_run_new_division_children(div_spec: DivisionSpec, result_inner: ReconcileResult) -> None:
        for dspec in div_spec.disciplines:
            disc = _find_discipline_by_global_name(dspec.name)
            if disc is not None:
                result_inner.disciplines_skipped += 1
                logger.info("[dry-run] discipline already exists: %s", dspec.name)
            else:
                result_inner.disciplines_created += 1
                disc = None
            if disc is None:
                for _c in dspec.collections:
                    result_inner.collections_created += 1
                continue
            for c in dspec.collections:
                if _find_collection(disc.id, c.code) is not None:
                    result_inner.collections_skipped += 1
                else:
                    result_inner.collections_created += 1

    result = ReconcileResult()
    try:
        institution = _institution_by_name(config.institution_name)
    except LookupError as e:
        result.errors.append(str(e))
        return result

    for div_spec in config.divisions:
        div = _find_division(institution.id, div_spec.name)
        if div is None:
            if dry_run:
                result.divisions_created += 1
                _dry_run_new_division_children(div_spec, result)
                continue
            div = _create_division_row(institution, div_spec, result)
            if div is None:
                for dspec in div_spec.disciplines:
                    for c in dspec.collections:
                        result.errors.append(
                            f"collection {c.code!r}: skipped (division {div_spec.name!r} not created)"
                        )
                continue
        else:
            result.divisions_skipped += 1
            logger.info("Division exists: %s (id=%s)", div_spec.name, div.id)

        for dspec in div_spec.disciplines:
            disc = _find_discipline_by_global_name(dspec.name)
            if disc is not None:
                result.disciplines_skipped += 1
                if disc.division_id != div.id:
                    result.warnings.append(
                        f"Discipline {dspec.name!r} exists on division_id={disc.division_id} "
                        f"but YAML nests it under {div_spec.name!r} (id={div.id}). "
                        "Skipping discipline create; applying collections to the existing discipline row."
                    )
                logger.info("Discipline exists: %s (id=%s)", dspec.name, disc.id)
            else:
                if dry_run:
                    result.disciplines_created += 1
                    disc = None
                else:
                    disc = _create_discipline_row(div, dspec, result)
                    if disc is None:
                        for c in dspec.collections:
                            result.errors.append(
                                f"collection {c.code!r}: skipped (discipline {dspec.name!r} not created)"
                            )
                        continue

            if disc is None:
                for c in dspec.collections:
                    if dry_run:
                        result.collections_created += 1
                    else:
                        result.errors.append(
                            f"collection {c.code!r}: skipped (discipline {dspec.name!r} missing in dry_run=false)"
                        )
                continue

            for c in dspec.collections:
                _ensure_collection(
                    disc,
                    c.code,
                    c.name,
                    c.catalognumberformatname,
                    dry_run_inner=dry_run,
                    result_inner=result,
                )

    return result
