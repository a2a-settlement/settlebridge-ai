"""Bounty leases: claim, renew, release, submit, and expiry."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


async def expire_stale_leases(db: AsyncSession) -> int:
    """Expire work leases past their window and submitted holds past their hold. Commits."""
    raise NotImplementedError
