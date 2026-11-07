"""Hide bounties on non-public GitHub repos, except from users who can read them.

Anonymous callers and users without a linked GitHub account only see public
bounties. A signed-in user with a linked GitHub identity can also see bounties
on private or internal repos when that identity has read access or higher,
matched by GitHub user id. GitHub errors fail closed and are not cached.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable

from sqlalchemy import literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.bounty import Bounty
from app.models.github import (
    ACTIVE_LEASE_STATUSES,
    PUBLIC_VISIBILITY,
    GitHubBountyBinding,
    GitHubLease,
    GitHubRepoLink,
)
from app.models.user import User
from app.services import github_app
from app.services.github_identity import get_identity_for_user

READ_PERMISSIONS = frozenset({"read", "triage", "write", "maintain", "admin"})
_ACCESS_TTL_SECONDS = 300
_CACHE_MAX = 10000
_REPORTED_VISIBILITY = frozenset({PUBLIC_VISIBILITY, "private", "internal"})
_MISSING = object()

# (repo_full_name, github_user_id) -> (expires_at, allowed). Oldest entry is first.
_access_cache: OrderedDict[tuple[str, int], tuple[float, bool]] = OrderedDict()


def _public_link_exists():
    """True when the binding's repo has a link whose visibility is exactly public."""
    return (
        select(literal(1))
        .where(
            GitHubRepoLink.repo_full_name == GitHubBountyBinding.repo_full_name,
            GitHubRepoLink.visibility == PUBLIC_VISIBILITY,
        )
        .correlate(GitHubBountyBinding)
        .exists()
    )


def bounty_hidden_clause() -> ColumnElement[bool]:
    """True when this bounty has a binding whose repo is missing or not public.

    Bounties with no GitHub binding are not hidden. NULL, private, and internal
    visibility are hidden. Only ``visibility == 'public'`` is public.
    """
    return (
        select(literal(1))
        .where(
            GitHubBountyBinding.bounty_id == Bounty.id,
            ~_public_link_exists(),
        )
        .correlate(Bounty)
        .exists()
    )


def bounty_public_clause() -> ColumnElement[bool]:
    return ~bounty_hidden_clause()


def bounty_visible_clause(readable_repos: set[str]) -> ColumnElement[bool]:
    """Public bounties, plus bounties bound to a repo this caller can read.

    An empty set is the anonymous rule: ``bounty_public_clause()``. Bounties
    with no GitHub binding stay visible.
    """
    if not readable_repos:
        return bounty_public_clause()
    names = tuple(sorted(readable_repos))
    readable = (
        select(literal(1))
        .where(
            GitHubBountyBinding.bounty_id == Bounty.id,
            GitHubBountyBinding.repo_full_name.in_(names),
        )
        .correlate(Bounty)
        .exists()
    )
    return or_(bounty_public_clause(), readable)


async def hidden_bounty_ids(
    db: AsyncSession, bounty_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    ids = list(bounty_ids)
    if not ids:
        return set()
    stmt = select(Bounty.id).where(Bounty.id.in_(ids), bounty_hidden_clause())
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def _is_requester(db: AsyncSession, bounty_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    stmt = select(Bounty.id).where(Bounty.id == bounty_id, Bounty.requester_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _is_funder(db: AsyncSession, bounty_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    stmt = (
        select(GitHubRepoLink.id)
        .join(
            GitHubBountyBinding,
            GitHubRepoLink.repo_full_name == GitHubBountyBinding.repo_full_name,
        )
        .where(
            GitHubBountyBinding.bounty_id == bounty_id,
            GitHubRepoLink.funder_user_id == user_id,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def _has_active_lease(db: AsyncSession, bounty_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    stmt = (
        select(GitHubLease.id)
        .join(GitHubBountyBinding, GitHubLease.binding_id == GitHubBountyBinding.id)
        .where(
            GitHubBountyBinding.bounty_id == bounty_id,
            GitHubLease.user_id == user_id,
            GitHubLease.status.in_(ACTIVE_LEASE_STATUSES),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


def _cache_get(key: tuple[str, int]):
    item = _access_cache.get(key)
    if item is None:
        return _MISSING
    expires_at, allowed = item
    if time.monotonic() >= expires_at:
        _access_cache.pop(key, None)
        return _MISSING
    return allowed


def _cache_put(key: tuple[str, int], allowed: bool) -> None:
    _access_cache.pop(key, None)
    while len(_access_cache) >= _CACHE_MAX:
        _access_cache.popitem(last=False)
    _access_cache[key] = (time.monotonic() + _ACCESS_TTL_SECONDS, allowed)


async def reset_repo_access_cache() -> None:
    _access_cache.clear()


def _as_github_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _cache_decision(permission, returned_id, github_user_id: int) -> bool | None:
    """Access bit to cache, or None when the GitHub answer is not definitive."""
    if not isinstance(permission, str):
        return None
    parsed_id = _as_github_id(returned_id)
    if permission == "none":
        return False
    if permission in READ_PERMISSIONS:
        if parsed_id is None:
            return None
        return parsed_id == github_user_id
    if parsed_id is not None and parsed_id != github_user_id:
        return False
    return None


async def has_repo_read(db: AsyncSession, user: User | None, link) -> bool:
    """True when the link is public, or this user's GitHub id can read the repo.

    A definitive denial (permission ``none``, or a present user id that does not
    match) is cached. A read-level permission with no user id is not cached.
    ``GitHubAPIError`` and any other lookup error return False and are not cached.
    """
    if link is not None and getattr(link, "visibility", None) == PUBLIC_VISIBILITY:
        return True
    if user is None or link is None:
        return False
    identity = await get_identity_for_user(db, user.id)
    if identity is None:
        return False
    try:
        github_user_id = int(identity.github_user_id)
    except (TypeError, ValueError):
        return False
    key = (link.repo_full_name, github_user_id)
    cached = _cache_get(key)
    if cached is not _MISSING:
        return cached
    try:
        permission, returned_id = await github_app.get_collaborator_record(
            int(link.installation_id),
            link.repo_full_name,
            identity.login,
        )
    except Exception:
        return False
    decision = _cache_decision(permission, returned_id, github_user_id)
    if decision is None:
        return False
    _cache_put(key, decision)
    return decision


def _nonpublic_enabled():
    return or_(
        GitHubRepoLink.visibility.is_(None),
        GitHubRepoLink.visibility != PUBLIC_VISIBILITY,
    )


async def readable_private_repos(db: AsyncSession, user: User | None) -> set[str]:
    """Enabled non-public repos this user can read. Empty without a GitHub identity.

    Public links are not sent to GitHub.
    """
    if user is None:
        return set()
    identity = await get_identity_for_user(db, user.id)
    if identity is None:
        return set()
    stmt = select(GitHubRepoLink).where(
        GitHubRepoLink.enabled.is_(True),
        _nonpublic_enabled(),
    )
    links = (await db.execute(stmt)).scalars().all()
    readable: set[str] = set()
    for link in links:
        if await has_repo_read(db, user, link):
            readable.add(link.repo_full_name)
    return readable


async def _enabled_nonpublic_links(db: AsyncSession, bounty_id: uuid.UUID) -> list:
    stmt = (
        select(GitHubRepoLink)
        .join(
            GitHubBountyBinding,
            GitHubRepoLink.repo_full_name == GitHubBountyBinding.repo_full_name,
        )
        .where(
            GitHubBountyBinding.bounty_id == bounty_id,
            GitHubRepoLink.enabled.is_(True),
            _nonpublic_enabled(),
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def can_view_bounty(db: AsyncSession, bounty_id: uuid.UUID, user: User | None) -> bool:
    """True when the bounty is public, or the user may see this non-public bounty.

    Requester, funder, and active lessee keep access. A signed-in collaborator
    also sees it when an enabled repo link is readable. Disabled private repos
    stay hidden except for those three.
    """
    if bounty_id not in await hidden_bounty_ids(db, [bounty_id]):
        return True
    if user is None:
        return False
    if await _is_requester(db, bounty_id, user.id):
        return True
    if await _is_funder(db, bounty_id, user.id):
        return True
    if await _has_active_lease(db, bounty_id, user.id):
        return True
    for link in await _enabled_nonpublic_links(db, bounty_id):
        if await has_repo_read(db, user, link):
            return True
    return False


async def github_visibility_for_bounties(
    db: AsyncSession, bounty_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Bounty id to ``public``, ``private``, or ``internal``. Unknown and null are omitted."""
    ids = list(bounty_ids)
    if not ids:
        return {}
    stmt = (
        select(GitHubBountyBinding.bounty_id, GitHubRepoLink.visibility)
        .join(
            GitHubRepoLink,
            GitHubRepoLink.repo_full_name == GitHubBountyBinding.repo_full_name,
        )
        .where(
            GitHubBountyBinding.bounty_id.in_(ids),
            GitHubRepoLink.visibility.in_(tuple(sorted(_REPORTED_VISIBILITY))),
        )
    )
    found: dict[uuid.UUID, str] = {}
    for bounty_id, visibility in (await db.execute(stmt)).all():
        if visibility in _REPORTED_VISIBILITY:
            found.setdefault(bounty_id, visibility)
    return found
