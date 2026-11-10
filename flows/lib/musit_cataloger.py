"""Resolve MUSIT ``REG_USER`` (BRUKARAR login) to Specify ``CollectionObject.cataloger``."""

from __future__ import annotations

from typing import Any


def normalize_musit_login(value: Any) -> str | None:
    """Normalize MUSIT ``BRUKARAR.USR`` / ``REG_USER`` for SpecifyUser lookup."""
    if value is None:
        return None
    text = str(value).strip()
    return text.upper() if text else None


def load_cataloger_agent_cache() -> dict[str, int]:
    """Load ``{SpecifyUser.name.upper(): agent_pk}`` for REG_USER → cataloger."""
    from specifyweb.specify.models import Agent

    cache: dict[str, int] = {}
    qs = (
        Agent.objects.filter(specifyuser__isnull=False)
        .exclude(specifyuser__name__isnull=True)
        .exclude(specifyuser__name="")
        .values_list("id", "specifyuser__name")
    )
    for agent_pk, username in qs.iterator(chunk_size=5000):
        key = normalize_musit_login(username)
        if key:
            # First Agent linked to the login wins (migrate_users creates one).
            cache.setdefault(key, int(agent_pk))
    return cache


def resolve_cataloger_agent(
    reg_user: Any,
    cataloger_cache: dict[str, int] | None = None,
) -> Any:
    """Resolve MUSIT ``REG_USER`` to the Specify ``Agent`` linked to that login."""
    key = normalize_musit_login(reg_user)
    if not key:
        return None
    from specifyweb.specify.models import Agent

    if cataloger_cache is not None:
        pk = cataloger_cache.get(key)
        if pk is None:
            return None
        return Agent(id=int(pk))

    from specifyweb.specify.models import Specifyuser

    user = Specifyuser.objects.filter(name__iexact=key).first()
    if user is None:
        return None
    return Agent.objects.filter(specifyuser=user).order_by("id").first()
