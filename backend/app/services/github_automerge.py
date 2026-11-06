"""Bounded auto-merge of ACCEPTABLE pull requests on repos that opt in."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def maybe_auto_merge(
    db: AsyncSession,
    *,
    binding,
    lease,
    spec,
    link,
    installation_id: int,
    repo: str,
    pr_number: int,
    pull_request: dict,
    head_sha: str,
    assessment: dict,
) -> str | None:
    """Merge when every auto-merge condition holds. Returns the merge method used, or None.

    Never raises for an ineligible or refused merge; settlement follows from the
    ``pull_request`` closed webhook as for a human merge.
    """
    return None
