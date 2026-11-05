"""Crash-safe GitHub job queue.

Claim with FOR UPDATE SKIP LOCKED, run the bounty dispatcher, and retry with
a deterministic exponential backoff. The webhook request never does this work.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.github import GitHubJob, JobStatus
from app.services.github_bounty_service import handle_job
from app.services.github_errors import PermanentJobError

logger = logging.getLogger(__name__)

_MAX_ERROR_CHARS = 2000
_BACKOFF_BASE_S = 10
_BACKOFF_CAP_S = 60 * 60


def compute_backoff(attempts: int) -> timedelta:
    """Exponential delay: min(2**attempts * 10s, 1h). No jitter."""
    seconds = min((2**attempts) * _BACKOFF_BASE_S, _BACKOFF_CAP_S)
    return timedelta(seconds=seconds)


def failure_transition(
    attempts: int, max_attempts: int, permanent: bool
) -> tuple[str, timedelta | None]:
    """Return (new status, delay). Delay is None when the job will not be retried."""
    if permanent:
        return JobStatus.FAILED.value, None
    if attempts >= max_attempts:
        return JobStatus.DEAD.value, None
    return JobStatus.PENDING.value, compute_backoff(attempts)


def _due_jobs_stmt(now: datetime, limit: int):
    due_pending = and_(
        GitHubJob.status == JobStatus.PENDING.value,
        GitHubJob.next_run_at <= now,
    )
    stale_running = and_(
        GitHubJob.status == JobStatus.RUNNING.value,
        GitHubJob.locked_until < now,
    )
    return (
        select(GitHubJob)
        .where(or_(due_pending, stale_running))
        .order_by(GitHubJob.next_run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


async def claim_due_jobs(
    db: AsyncSession, limit: int, lock_seconds: int = 300
) -> list[uuid.UUID]:
    """Lock due jobs, mark them running, and commit the claim."""
    now = datetime.now(timezone.utc)
    result = await db.execute(_due_jobs_stmt(now, limit))
    jobs = result.scalars().all()
    lock_until = now + timedelta(seconds=lock_seconds)
    ids: list[uuid.UUID] = []
    for job in jobs:
        job.status = JobStatus.RUNNING.value
        job.attempts = int(job.attempts or 0) + 1
        job.locked_until = lock_until
        ids.append(job.id)
    await db.commit()
    return ids


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for secret in (settings.GITHUB_WEBHOOK_SECRET, settings.GITHUB_APP_PRIVATE_KEY):
        if secret:
            text = text.replace(secret, "[redacted]")
    if len(text) > _MAX_ERROR_CHARS:
        return text[:_MAX_ERROR_CHARS]
    return text


async def _record_failure(session_factory, job_id: uuid.UUID, exc: Exception) -> None:
    message = _safe_error(exc)
    permanent = isinstance(exc, PermanentJobError)
    async with session_factory() as db:
        job = await db.get(GitHubJob, job_id)
        if job is None:
            logger.error("GitHub job %s missing while recording failure", job_id)
            return
        new_status, backoff = failure_transition(
            int(job.attempts or 0),
            settings.GITHUB_JOB_MAX_ATTEMPTS,
            permanent,
        )
        job.status = new_status
        job.last_error = message
        job.locked_until = None
        if backoff is not None:
            job.next_run_at = datetime.now(timezone.utc) + backoff
        await db.commit()
    logger.error("GitHub job %s -> %s: %s", job_id, new_status, message)


async def run_job(session_factory, job_id: uuid.UUID) -> None:
    """Run one claimed job. Handler failures are recorded in a fresh transaction."""
    async with session_factory() as db:
        job = await db.get(GitHubJob, job_id)
        if job is None:
            logger.error("GitHub job %s not found", job_id)
            return
        try:
            await handle_job(db, job)
        except Exception as exc:
            await db.rollback()
            await _record_failure(session_factory, job_id, exc)
            return
        # Settlement commits per step; reload so this write binds to the live row.
        job = await db.get(GitHubJob, job_id)
        if job is None:
            logger.error("GitHub job %s disappeared after handler", job_id)
            return
        job.status = JobStatus.DONE.value
        job.locked_until = None
        job.last_error = None
        await db.commit()
    logger.info("GitHub job %s done", job_id)


async def run_due_jobs(session_factory=async_session, limit: int = 10) -> int:
    """Claim up to `limit` jobs and run them one at a time. Returns how many ran."""
    async with session_factory() as db:
        job_ids = await claim_due_jobs(db, limit)
    for job_id in job_ids:
        await run_job(session_factory, job_id)
    return len(job_ids)


async def run_github_job_loop() -> None:
    """Poll forever. An iteration error is logged; the loop stays up."""
    logger.info(
        "GitHub job worker started (poll every %ss)",
        settings.GITHUB_JOB_POLL_INTERVAL_S,
    )
    while True:
        try:
            await run_due_jobs()
        except Exception:
            logger.exception("GitHub job worker iteration failed")
        await asyncio.sleep(settings.GITHUB_JOB_POLL_INTERVAL_S)
