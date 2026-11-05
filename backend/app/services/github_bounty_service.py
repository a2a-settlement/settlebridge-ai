"""GitHub bounty lifecycle: labels, frozen specs, evaluation, merge acceptance."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github import GitHubJob


async def handle_job(db: AsyncSession, job: GitHubJob) -> None:
    raise NotImplementedError
