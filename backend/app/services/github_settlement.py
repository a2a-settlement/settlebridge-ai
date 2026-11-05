"""Idempotent, resumable settlement of accepted GitHub bounties."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github import GitHubSettlement


async def advance_settlement(db: AsyncSession, settlement_id: uuid.UUID) -> GitHubSettlement:
    raise NotImplementedError
