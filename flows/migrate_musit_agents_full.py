"""Prefect flow: import MUSIT actors in one pass.

Creates each Specify ``Agent`` together with its detail fields (address, dates,
URL, notes) and alternate ``AgentVariant`` spellings. Replaces running
``Migrate MUSIT Actors``, ``Migrate MUSIT Agent Details``, and
``Migrate MUSIT Agent Variants`` as three separate deployments.

An actor that already has the remarks marker is skipped, so a re-run after a
crash continues with the actors that were not committed. Each actor is written
in one database transaction.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_MUSIT_AGENTS_FULL,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.musit_agent_bundle import (
    alternate_variant_names,
    bundle_from_joined_rows,
    pick_preferred_person_name,
    sql_actors_with_person_names,
)
from flows.lib.musit_agent_details import build_agent_details_patch, trunc
from flows.lib.musit_agent_names import choose_agent_name_parts
from flows.lib.musit_agent_variants import AGENT_VARIANT_VARTYPE_VARIANT
from flows.lib.musit_agents_common import (
    ALLOWED_MUSIT_AGENT_SCHEMAS,
    build_musit_actor_remarks,
    load_actor_id_to_agent_id,
    musit_actor_remarks_marker,
)
from flows.lib.oracle_connectivity import (
    create_oracle_connection,
    get_oracle_config_from_env,
)
from flows.lib.specify_setup import setup_django
from flows.migrate_musit_agents import musit_actor_type_to_specify_agent_type

_MAX_ERROR_LINES = 200
_PROGRESS_LOG_EVERY = 5000


@dataclass
class MusitAgentsFullResult:
    actors_seen: int = 0
    agents_created: int = 0
    agents_skipped: int = 0
    variants_created: int = 0
    addresses_created: int = 0
    identifiers_created: int = 0
    errors: list[str] = field(default_factory=list)
    schemas_processed: list[str] = field(default_factory=list)


@dataclass
class MusitAgentsFullOutcome:
    result: MusitAgentsFullResult
    oracle_rows: int
    oracle_rows_per_schema: dict[str, int]


def _append_error(result: MusitAgentsFullResult, msg: str) -> None:
    if len(result.errors) < _MAX_ERROR_LINES:
        result.errors.append(msg)
    elif len(result.errors) == _MAX_ERROR_LINES:
        result.errors.append(
            f"... further errors omitted (cap {_MAX_ERROR_LINES}); see worker logs"
        )


def _remarks_for(bundle, schema: str, patch_remarks: str | None) -> str:
    if patch_remarks:
        return patch_remarks
    return build_musit_actor_remarks(
        schema,
        bundle.actor_id,
        institution=trunc(bundle.institution, 500),
        oracle_note=None,
    )


def _write_actor(bundle, schema: str, division, *, dry_run: bool, result: MusitAgentsFullResult) -> None:
    from django.db import transaction

    from specifyweb.specify.models import Address, Agent, Agentidentifier, Agentvariant

    preferred = pick_preferred_person_name(bundle.person_names, bundle.actorname)
    agent_type = musit_actor_type_to_specify_agent_type(bundle.actor_type)
    parts = choose_agent_name_parts(
        person_given_name=preferred.given if preferred else None,
        person_surname=preferred.surname if preferred else None,
        person_middle_name=preferred.middle if preferred else None,
        title=preferred.title if preferred else None,
        actorname=bundle.actorname,
        agent_type=agent_type,
    )
    marker = musit_actor_remarks_marker(schema, bundle.actor_id)
    patch = build_agent_details_patch(
        bundle.details_row(),
        schema=schema,
        actor_id=bundle.actor_id,
        agent_email=None,
        agent_url=None,
        agent_dob=None,
        agent_dod=None,
        agent_text1=None,
        agent_text2=None,
        agent_text3=None,
        agent_text4=None,
        agent_text5=None,
        agent_remarks=marker,
        has_address=False,
        existing_identifier_keys=set(),
    )
    variants = alternate_variant_names(bundle.person_names, bundle.actorname)
    remarks = _remarks_for(bundle, schema, patch.remarks)

    if dry_run:
        result.agents_created += 1
        result.variants_created += len(variants)
        if patch.create_address:
            result.addresses_created += 1
        result.identifiers_created += len(patch.identifiers)
        return

    agent_fields: dict[str, Any] = {
        "agenttype": agent_type,
        "firstname": parts.firstname,
        "lastname": parts.lastname or "(unknown)",
        "middleinitial": parts.middleinitial,
        "title": parts.title,
        "division": division,
        "specifyuser": None,
        "remarks": remarks,
    }
    agent_fields.update(patch.agent_fields)

    with transaction.atomic():
        agent = Agent(**agent_fields)
        agent.save()
        if patch.create_address:
            Address.objects.create(agent_id=agent.id, **patch.address_fields)
            result.addresses_created += 1
        for ident in patch.identifiers:
            Agentidentifier.objects.create(
                agent_id=agent.id,
                identifier=ident.identifier,
                identifiertype=ident.identifier_type,
            )
            result.identifiers_created += 1
        for name in variants:
            Agentvariant.objects.create(
                agent_id=agent.id,
                name=name,
                vartype=AGENT_VARIANT_VARTYPE_VARIANT,
            )
            result.variants_created += 1
    result.agents_created += 1


@task(retries=1, retry_delay_seconds=5)
def import_musit_agents_full_task(
    oracle_env: str,
    schemas: list[str],
    dry_run: bool = True,
) -> MusitAgentsFullOutcome:
    """Stream actors with their person names and write Agent + details + variants."""
    setup_django()
    logger = get_run_logger()
    from specifyweb.specify.models import Division

    division = Division.objects.first()
    if division is None:
        raise RuntimeError("No Division found in Specify — the database must be initialized first")

    for schema in schemas:
        if schema not in ALLOWED_MUSIT_AGENT_SCHEMAS:
            raise ValueError(f"Unsupported schema: {schema}")

    result = MusitAgentsFullResult()
    rows_per_schema: Counter[str] = Counter()
    oracle_rows = 0

    config = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config)
    try:
        for schema in schemas:
            existing = set(load_actor_id_to_agent_id(schema))
            logger.info(f"{schema}: {len(existing)} agents already have MUSIT markers")
            sql = sql_actors_with_person_names(schema)
            with connection.cursor() as cur:
                cur.execute(sql)
                cols = [d[0].lower() for d in cur.description]
                current_id: int | None = None
                bucket: list[dict[str, Any]] = []

                def flush() -> None:
                    if not bucket:
                        return
                    bundle = bundle_from_joined_rows(bucket)
                    result.actors_seen += 1
                    if bundle.actor_id in existing:
                        result.agents_skipped += 1
                        return
                    try:
                        _write_actor(
                            bundle,
                            schema,
                            division,
                            dry_run=dry_run,
                            result=result,
                        )
                        existing.add(bundle.actor_id)
                    except Exception as exc:
                        msg = f"Error importing {schema}.ACTOR_ID={bundle.actor_id}: {exc}"
                        logger.error(msg)
                        _append_error(result, msg)
                    if result.actors_seen % _PROGRESS_LOG_EVERY == 0:
                        logger.info(
                            f"{'[DRY RUN] ' if dry_run else ''}progress: "
                            f"{result.actors_seen} actors "
                            f"(created={result.agents_created}, "
                            f"skipped={result.agents_skipped}, "
                            f"variants={result.variants_created})"
                        )

                for raw in cur:
                    oracle_rows += 1
                    rows_per_schema[schema] += 1
                    row = dict(zip(cols, raw))
                    actor_id = int(row["actor_id"])
                    if current_id is None:
                        current_id = actor_id
                    if actor_id != current_id:
                        flush()
                        bucket = [row]
                        current_id = actor_id
                    else:
                        bucket.append(row)
                flush()
            logger.info(f"Finished {schema} ({rows_per_schema[schema]} joined rows)")
    finally:
        connection.close()

    result.schemas_processed = sorted(schemas)
    logger.info(
        f"Full MUSIT agent import done: actors={result.actors_seen}, "
        f"created={result.agents_created}, skipped={result.agents_skipped}, "
        f"variants={result.variants_created}, addresses={result.addresses_created}, "
        f"identifiers={result.identifiers_created}, errors={len(result.errors)}"
    )
    return MusitAgentsFullOutcome(
        result=result,
        oracle_rows=oracle_rows,
        oracle_rows_per_schema=dict(sorted(rows_per_schema.items())),
    )


def _report_dict(
    ts: str,
    oracle_env: str,
    dry_run: bool,
    musit_schemas: list[str],
    outcome: MusitAgentsFullOutcome,
) -> dict:
    r = outcome.result
    return {
        "report_version": 1,
        "flow": "migrate_musit_agents_full",
        "migration_phase": "1.1",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "musit_schemas": list(musit_schemas),
        "oracle_rows": outcome.oracle_rows,
        "oracle_rows_per_schema": outcome.oracle_rows_per_schema,
        "actors_seen": r.actors_seen,
        "agents_created": r.agents_created,
        "agents_skipped": r.agents_skipped,
        "variants_created": r.variants_created,
        "addresses_created": r.addresses_created,
        "identifiers_created": r.identifiers_created,
        "schemas_processed": r.schemas_processed,
        "errors": r.errors,
    }


@flow(
    name="Migrate MUSIT Actors Full",
    description=(
        "Import MUSIT ACTOR rows as Specify Agents, including detail fields "
        "and alternate name variants, in one run."
    ),
)
def migrate_musit_agents_full_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    musit_schemas: list[str] | None = None,
) -> dict:
    logger = get_run_logger()
    if musit_schemas is None:
        musit_schemas = ["MUSIT_BOTANIKK_FELLES", "MUSIT_ZOOLOGI_ENTOMOLOGI"]
    for schema in musit_schemas:
        if schema not in ALLOWED_MUSIT_AGENT_SCHEMAS:
            raise ValueError(
                f"Invalid musit_schemas entry {schema!r}; "
                f"allowed: {sorted(ALLOWED_MUSIT_AGENT_SCHEMAS)}"
            )

    logger.info(
        f"Starting full MUSIT agent import "
        f"(oracle_env={oracle_env}, dry_run={dry_run}, schemas={musit_schemas})"
    )
    setup_django()
    outcome = import_musit_agents_full_task(oracle_env, list(musit_schemas), dry_run)
    r = outcome.result

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = _report_dict(ts, oracle_env, dry_run, musit_schemas, outcome)
    s3_key = migration_report_s3_key(REPORT_CATEGORY_MUSIT_AGENTS_FULL, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info(f"Uploaded report: {uri}")

    return {
        "agents_created": r.agents_created,
        "agents_skipped": r.agents_skipped,
        "variants_created": r.variants_created,
        "addresses_created": r.addresses_created,
        "identifiers_created": r.identifiers_created,
        "actors_seen": r.actors_seen,
        "errors": r.errors,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
    }
