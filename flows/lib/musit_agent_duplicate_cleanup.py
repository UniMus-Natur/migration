"""Helpers to repair mangled MUSIT agent remarks and delete unused duplicates.

Background
----------
``migrate_musit_agent_details`` used to rewrite remarks to
``MUSIT-migration: ACTOR; oracle_note=…``, dropping ``schema`` / ``ACTOR_ID``.
A later ``update_existing_names`` run then failed to match those Agents and
created new ones with a proper marker. Specimens are not linked yet; the
orphans still hold AgentVariant / address / identifier rows.

Cleanup policy
--------------
For each MUSIT ``ACTOR``: keep the Agent with the richest related data
(variants first, then addresses/identifiers, then oldest id), restore a full
remarks marker, refresh name fields from ``ACTORNAME``, and delete unused
duplicates that have no specimen-content FKs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from flows.lib.musit_agent_names import choose_agent_name_parts
from flows.lib.musit_agents_common import (
    build_musit_actor_remarks,
    extract_institution_from_remarks,
    extract_oracle_note_from_remarks,
    is_oracle_note_only_remarks,
    musit_actor_remarks_marker,
    parse_actor_marker,
)

# Content FKs that mean "this agent is used on collection data".
# Audit CreatedBy/ModifiedBy columns are ignored for delete safety.
_CONTENT_FK_CHECKS: tuple[tuple[str, str], ...] = (
    ("collector", "AgentID"),
    ("determiner", "AgentID"),
    ("determination", "DeterminerID"),
    ("collectionobject", "CatalogerID"),
    ("collectionobject", "InventorizedByID"),
    ("collectionobject", "Agent1ID"),
    ("author", "AgentID"),
    ("accessionagent", "AgentID"),
    ("groupperson", "MemberID"),
    ("groupperson", "GroupID"),
)


@dataclass
class AgentCleanupCandidate:
    agent_id: int
    remarks: str | None
    firstname: str | None
    lastname: str | None
    middleinitial: str | None
    title: str | None
    text1: str | None
    variant_count: int = 0
    address_count: int = 0
    identifier_count: int = 0


@dataclass
class ActorCleanupPlan:
    schema: str
    actor_id: int
    actorname: str | None
    actor_type: int | None
    keeper_id: int
    delete_ids: list[int] = field(default_factory=list)
    restore_remarks: str | None = None
    name_updates: dict[str, str | None] = field(default_factory=dict)
    skip_reason: str | None = None


@dataclass
class MusitAgentDuplicateCleanupResult:
    actors_seen: int = 0
    actors_with_candidates: int = 0
    keepers_repaired: int = 0
    keepers_unchanged: int = 0
    duplicates_deleted: int = 0
    skipped_blocked_fk: int = 0
    orphans_unmatched: int = 0
    errors: list[str] = field(default_factory=list)
    schemas_processed: list[str] = field(default_factory=list)


def normalize_actorname(value: str | None) -> str | None:
    if value is None:
        return None
    t = " ".join(str(value).split())
    return t.casefold() if t else None


def keeper_sort_key(c: AgentCleanupCandidate) -> tuple:
    """Higher is better keeper; break ties with lower agent id."""
    return (c.variant_count, c.address_count, c.identifier_count, -c.agent_id)


def pick_keeper(candidates: list[AgentCleanupCandidate]) -> AgentCleanupCandidate:
    return max(candidates, key=keeper_sort_key)


def desired_name_fields(
    *,
    actorname: str | None,
    actor_type: int | None,
) -> dict[str, str | None]:
    """Name fields from ACTORNAME only (PERSON_NAME not needed for cleanup)."""
    # MUSIT 0=person → Specify 1; MUSIT 1/2 org/group → Specify 0
    if actor_type in (1, 2):
        specify_type = 0
    else:
        specify_type = 1
    parts = choose_agent_name_parts(
        person_given_name=None,
        person_surname=None,
        person_middle_name=None,
        title=None,
        actorname=actorname,
        agent_type=specify_type,
    )
    return {
        "firstname": parts.firstname,
        "lastname": parts.lastname,
        "middleinitial": parts.middleinitial,
        "title": parts.title,
    }


def name_field_updates(
    candidate: AgentCleanupCandidate,
    desired: dict[str, str | None],
) -> dict[str, str | None]:
    changed: dict[str, str | None] = {}
    for field_name, want in desired.items():
        have = getattr(candidate, field_name, None)
        have_n = (str(have).strip() if have is not None else "") or None
        want_n = (str(want).strip() if want is not None else "") or None
        if have_n != want_n:
            changed[field_name] = want
    return changed


def planned_remarks_for_keeper(
    schema: str,
    actor_id: int,
    candidates: list[AgentCleanupCandidate],
) -> str:
    """Rebuild full marker; prefer oracle_note/institution from any candidate."""
    note = None
    institution = None
    for c in candidates:
        if note is None:
            note = extract_oracle_note_from_remarks(c.remarks)
        if institution is None:
            institution = extract_institution_from_remarks(c.remarks)
    return build_musit_actor_remarks(
        schema,
        actor_id,
        institution=institution,
        oracle_note=note,
    )


def plan_actor_cleanup(
    *,
    schema: str,
    actor_id: int,
    actorname: str | None,
    actor_type: int | None,
    candidates: list[AgentCleanupCandidate],
) -> ActorCleanupPlan | None:
    if not candidates:
        return None
    keeper = pick_keeper(candidates)
    delete_ids = [c.agent_id for c in candidates if c.agent_id != keeper.agent_id]
    desired = desired_name_fields(actorname=actorname, actor_type=actor_type)
    updates = name_field_updates(keeper, desired)
    new_remarks = planned_remarks_for_keeper(schema, actor_id, candidates)
    restore = None if (keeper.remarks or "") == new_remarks else new_remarks
    return ActorCleanupPlan(
        schema=schema,
        actor_id=actor_id,
        actorname=actorname,
        actor_type=actor_type,
        keeper_id=keeper.agent_id,
        delete_ids=delete_ids,
        restore_remarks=restore,
        name_updates=updates,
    )


def sql_actors_for_cleanup(schema: str) -> str:
    sch = schema.strip().upper()
    return f"""
        SELECT
            a.ACTOR_ID,
            a.ACTOR_TYPE,
            a.ACTORNAME
        FROM {sch}.ACTOR a
        ORDER BY a.ACTOR_ID
    """


def agent_has_blocking_content_fks(agent_id: int, cursor) -> list[str]:
    """Return human-readable FK hits that should block deletion."""
    hits: list[str] = []
    for table, column in _CONTENT_FK_CHECKS:
        cursor.execute(
            f"SELECT COUNT(*) FROM `{table}` WHERE `{column}` = %s",
            [agent_id],
        )
        row = cursor.fetchone()
        count = int(row[0]) if row else 0
        if count:
            hits.append(f"{table}.{column}={count}")
    return hits


def _related_counts(agent_ids: list[int]) -> dict[int, tuple[int, int, int]]:
    """Map agent_id → (variant_count, address_count, identifier_count)."""
    from django.db.models import Count

    from specifyweb.specify.models import Address, Agentidentifier, Agentvariant

    if not agent_ids:
        return {}

    def _counts(model) -> dict[int, int]:
        out: dict[int, int] = {}
        # Chunk to avoid huge IN lists.
        chunk = 2000
        for i in range(0, len(agent_ids), chunk):
            part = agent_ids[i : i + chunk]
            for agent_id, n in (
                model.objects.filter(agent_id__in=part)
                .values("agent_id")
                .annotate(n=Count("id"))
                .values_list("agent_id", "n")
            ):
                out[int(agent_id)] = int(n)
        return out

    variants = _counts(Agentvariant)
    addresses = _counts(Address)
    identifiers = _counts(Agentidentifier)
    return {
        aid: (variants.get(aid, 0), addresses.get(aid, 0), identifiers.get(aid, 0))
        for aid in agent_ids
    }


def load_all_musit_agent_indexes(
    schemas: list[str],
) -> tuple[
    dict[str, dict[int, list[AgentCleanupCandidate]]],
    dict[str, list[AgentCleanupCandidate]],
    list[AgentCleanupCandidate],
]:
    """Index all MUSIT Agents once.

    Returns:
        by_schema_actor: schema → actor_id → candidates with parseable markers
        orphans_by_text1: normalized text1 → oracle_note-only candidates
        unmatched_orphans: oracle_note-only candidates with empty text1
    """
    from specifyweb.specify.models import Agent

    schema_set = {s.strip().upper() for s in schemas}
    by_schema_actor: dict[str, dict[int, list[AgentCleanupCandidate]]] = {
        s: {} for s in schema_set
    }
    orphans_by_text1: dict[str, list[AgentCleanupCandidate]] = {}
    unmatched_orphans: list[AgentCleanupCandidate] = []

    raw_rows: list[tuple[Any, ...]] = []
    prefix = "MUSIT-migration: ACTOR"
    qs = Agent.objects.filter(remarks__startswith=prefix).only(
        "id",
        "remarks",
        "firstname",
        "lastname",
        "middleinitial",
        "title",
        "text1",
    )
    for agent in qs.iterator(chunk_size=5000):
        parsed = parse_actor_marker(agent.remarks)
        is_orphan = is_oracle_note_only_remarks(agent.remarks)
        if parsed is not None:
            sch, _actor_id = parsed
            if sch not in schema_set:
                continue
        elif not is_orphan:
            continue
        raw_rows.append(
            (
                int(agent.id),
                agent.remarks,
                agent.firstname,
                agent.lastname,
                agent.middleinitial,
                agent.title,
                agent.text1,
                parsed,
                is_orphan,
            )
        )

    counts = _related_counts([r[0] for r in raw_rows])
    for (
        agent_id,
        remarks,
        firstname,
        lastname,
        middleinitial,
        title,
        text1,
        parsed,
        is_orphan,
    ) in raw_rows:
        vc, ac, ic = counts.get(agent_id, (0, 0, 0))
        cand = AgentCleanupCandidate(
            agent_id=agent_id,
            remarks=remarks,
            firstname=firstname,
            lastname=lastname,
            middleinitial=middleinitial,
            title=title,
            text1=text1,
            variant_count=vc,
            address_count=ac,
            identifier_count=ic,
        )
        if parsed is not None:
            sch, actor_id = parsed
            by_schema_actor[sch].setdefault(actor_id, []).append(cand)
        elif is_orphan:
            key = normalize_actorname(text1)
            if key:
                orphans_by_text1.setdefault(key, []).append(cand)
            else:
                unmatched_orphans.append(cand)

    return by_schema_actor, orphans_by_text1, unmatched_orphans


def collect_candidates_for_actor(
    *,
    actor_id: int,
    actorname: str | None,
    by_actor: dict[int, list[AgentCleanupCandidate]],
    orphans_by_text1: dict[str, list[AgentCleanupCandidate]],
    claimed_orphan_ids: set[int],
) -> list[AgentCleanupCandidate]:
    """Merge marker-matched agents with an unused orphan matching ACTORNAME."""
    merged: dict[int, AgentCleanupCandidate] = {}
    for c in by_actor.get(actor_id, []):
        merged[c.agent_id] = c

    key = normalize_actorname(actorname)
    if key:
        for c in orphans_by_text1.get(key, []):
            if c.agent_id in claimed_orphan_ids:
                continue
            merged[c.agent_id] = c
            claimed_orphan_ids.add(c.agent_id)

    return list(merged.values())


def reassign_related_rows(from_agent_id: int, to_agent_id: int) -> None:
    """Move variants / addresses / identifiers onto the keeper before delete."""
    from specifyweb.specify.models import Address, Agentidentifier, Agentvariant

    if from_agent_id == to_agent_id:
        return

    # Variants: skip names already present on keeper.
    existing_names = set(
        Agentvariant.objects.filter(agent_id=to_agent_id).values_list("name", flat=True)
    )
    for av in Agentvariant.objects.filter(agent_id=from_agent_id).iterator():
        if av.name in existing_names:
            av.delete()
        else:
            av.agent_id = to_agent_id
            av.save(update_fields=["agent_id"])
            existing_names.add(av.name)

    Address.objects.filter(agent_id=from_agent_id).update(agent_id=to_agent_id)

    existing_ids = {
        (itype or "", ident or "")
        for itype, ident in Agentidentifier.objects.filter(
            agent_id=to_agent_id
        ).values_list("identifiertype", "identifier")
    }
    for ident in Agentidentifier.objects.filter(agent_id=from_agent_id).iterator():
        key = (ident.identifiertype or "", ident.identifier or "")
        if key in existing_ids:
            ident.delete()
        else:
            ident.agent_id = to_agent_id
            ident.save(update_fields=["agent_id"])
            existing_ids.add(key)


def apply_cleanup_plan(
    plan: ActorCleanupPlan,
    *,
    dry_run: bool,
    db_cursor,
    result: MusitAgentDuplicateCleanupResult,
) -> None:
    from specifyweb.specify.models import Agent

    # Block deletes that still have content FKs.
    blocked: list[str] = []
    for dupe_id in plan.delete_ids:
        hits = agent_has_blocking_content_fks(dupe_id, db_cursor)
        if hits:
            blocked.append(f"agent_id={dupe_id} ({', '.join(hits)})")
    if blocked:
        result.skipped_blocked_fk += 1
        msg = (
            f"Skip deletes for {plan.schema}.ACTOR_ID={plan.actor_id} "
            f"keeper={plan.keeper_id}: " + "; ".join(blocked)
        )
        if len(result.errors) < 200:
            result.errors.append(msg)
        return

    will_repair = bool(plan.restore_remarks or plan.name_updates)
    if not will_repair and not plan.delete_ids:
        result.keepers_unchanged += 1
        return

    if dry_run:
        if will_repair:
            result.keepers_repaired += 1
        elif not plan.delete_ids:
            result.keepers_unchanged += 1
        result.duplicates_deleted += len(plan.delete_ids)
        return

    try:
        if will_repair:
            keeper = Agent.objects.get(pk=plan.keeper_id)
            update_fields: list[str] = []
            if plan.restore_remarks is not None:
                keeper.remarks = plan.restore_remarks
                update_fields.append("remarks")
            for field_name, value in plan.name_updates.items():
                setattr(keeper, field_name, value)
                update_fields.append(field_name)
            keeper.timestampmodified = datetime.now(timezone.utc)
            update_fields.append("timestampmodified")
            keeper.save(update_fields=update_fields)
            result.keepers_repaired += 1
        elif not plan.delete_ids:
            result.keepers_unchanged += 1

        for dupe_id in plan.delete_ids:
            reassign_related_rows(dupe_id, plan.keeper_id)
            Agent.objects.filter(pk=dupe_id).delete()
            result.duplicates_deleted += 1
    except Exception as exc:
        msg = (
            f"Error cleaning {plan.schema}.ACTOR_ID={plan.actor_id} "
            f"keeper={plan.keeper_id}: {exc}"
        )
        if len(result.errors) < 200:
            result.errors.append(msg)
