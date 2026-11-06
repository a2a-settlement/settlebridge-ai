"""Hide bounties tied to a GitHub repo that is not public."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import literal, select
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


async def can_view_bounty(db: AsyncSession, bounty_id: uuid.UUID, user: User | None) -> bool:
    """True when the bounty is public, or the user is requester, funder, or active lessee."""
    if bounty_id not in await hidden_bounty_ids(db, [bounty_id]):
        return True
    if user is None:
        return False
    if await _is_requester(db, bounty_id, user.id):
        return True
    if await _is_funder(db, bounty_id, user.id):
        return True
    return await _has_active_lease(db, bounty_id, user.id)
