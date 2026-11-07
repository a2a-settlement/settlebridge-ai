"""Crash-safe GitHub job queue.

Claim with FOR UPDATE SKIP LOCKED, run the bounty or challenge dispatcher, and
retry with a deterministic exponential backoff. The webhook request never does
this work.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import ObjectDeletedError

from app.config import settings
from app.database import async_session
from app.models.github import (
    GitHubJob,
    GitHubSettlement,
    JobKind,
    JobStatus,
    SettlementState,
)
from app.services.github_bounty_service import handle_job
from app.services.github_errors import GitHubRateLimited, PermanentJobError
from app.services.github_leases import expire_stale_leases

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


async def claim_due_jobs(db: AsyncSession, limit: int, lock_seconds: int = 300) -> list[uuid.UUID]:
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for secret in (
        settings.GITHUB_WEBHOOK_SECRET,
        settings.GITHUB_APP_PRIVATE_KEY,
        settings.CHALLENGE_REPORT_SIGNING_KEY,
    ):
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
        attempts = int(job.attempts or 0)
        if isinstance(exc, GitHubRateLimited):
            # claim_due_jobs already counted this run; a rate limit must not spend it.
            attempts = max(0, attempts - 1)
            job.attempts = attempts
        new_status, backoff = failure_transition(
            attempts,
            settings.GITHUB_JOB_MAX_ATTEMPTS,
            permanent,
        )
        job.status = new_status
        job.last_error = message
        job.locked_until = None
        if backoff is not None:
            scheduled = datetime.now(timezone.utc) + backoff
            if isinstance(exc, GitHubRateLimited):
                scheduled = max(scheduled, _as_utc(exc.retry_at))
            job.next_run_at = scheduled
        await db.commit()
    logger.error("GitHub job %s -> %s: %s", job_id, new_status, message)


def _is_challenge(job: GitHubJob) -> bool:
    return _text(getattr(job, "kind", "")).startswith("challenge_")


async def _reload_job(db: AsyncSession, job_id: uuid.UUID) -> GitHubJob | None:
    """Re-read the row so a cancel committed during the handler is visible."""
    job = await db.get(GitHubJob, job_id)
    if job is None:
        return None
    refresh = getattr(db, "refresh", None)
    if refresh is None:
        return job
    outcome = refresh(job)
    if hasattr(outcome, "__await__"):
        try:
            await outcome
        except ObjectDeletedError:
            return None
    return job


async def _dispatch_job(db: AsyncSession, job: GitHubJob) -> bool:
    """Run the handler. False when this function already finalized the job."""
    if _is_challenge(job):
        if not settings.CHALLENGE_ENGINE_ENABLED:
            job.status = JobStatus.CANCELLED.value
            job.locked_until = None
            job.last_error = "challenge engine disabled"
            await db.commit()
            logger.info("GitHub job %s cancelled: challenge engine disabled", job.id)
            return False
        # Imported here: the orchestrator owns the handlers and may import this queue.
        from app.services.challenge_orchestrator import handle_challenge_job

        await handle_challenge_job(db, job)
        return True
    await handle_job(db, job)
    return True


async def run_job(session_factory, job_id: uuid.UUID) -> None:
    """Run one claimed job. Handler failures are recorded in a fresh transaction."""
    async with session_factory() as db:
        job = await db.get(GitHubJob, job_id)
        if job is None:
            logger.error("GitHub job %s not found", job_id)
            return
        if _text(getattr(job, "status", "")) == JobStatus.CANCELLED.value:
            logger.info("GitHub job %s cancelled; skipping", job_id)
            return
        try:
            finished = await _dispatch_job(db, job)
        except Exception as exc:
            await db.rollback()
            await _record_failure(session_factory, job_id, exc)
            return
        if not finished:
            return
        # Settlement commits per step; reload so this write binds to the live row.
        job = await _reload_job(db, job_id)
        if job is None:
            logger.error("GitHub job %s disappeared after handler", job_id)
            return
        if _text(getattr(job, "status", "")) == JobStatus.CANCELLED.value:
            logger.info("GitHub job %s cancelled while running", job_id)
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


# needs_review is omitted on purpose. The alert gate left that escrow locked, and
# sweeping it would enqueue a new advance forever while GitHub stays unreachable.
_RESUMABLE_SETTLEMENT_STATES = frozenset(
    {
        SettlementState.PENDING.value,
        SettlementState.ESCROW_CREATED.value,
        SettlementState.DELIVERED.value,
    }
)
_OPEN_JOB_STATUSES = frozenset({JobStatus.PENDING.value, JobStatus.RUNNING.value})


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    enumerated = getattr(value, "value", None)
    if isinstance(enumerated, str):
        return enumerated
    return str(value)


def _open_advance_targets(jobs) -> set[str]:
    covered: set[str] = set()
    for job in jobs:
        if _text(getattr(job, "kind", "")) != JobKind.ADVANCE_SETTLEMENT.value:
            continue
        if _text(getattr(job, "status", "")) not in _OPEN_JOB_STATUSES:
            continue
        payload = getattr(job, "payload", None)
        if not isinstance(payload, dict):
            continue
        raw = payload.get("settlement_id")
        if isinstance(raw, uuid.UUID):
            covered.add(str(raw))
        elif isinstance(raw, str) and raw:
            covered.add(raw)
    return covered


async def enqueue_pending_settlements(db: AsyncSession) -> int:
    """Queue an advance job for each open settlement that does not already have one.

    Settlement stays disabled until ``GITHUB_SETTLEMENT_ENABLED`` is true, and an
    advance job that runs while the flag is off is marked done. Re-queueing here
    lets those settlements resume after the flag is turned on. ``released`` and
    ``failed`` are terminal: a released row is finalized by the job that released
    it, and queueing another would enqueue forever. ``needs_review`` is not
    queued; a later advance re-enters the alert gate without a sweep job.
    Commits only when a job is inserted. Returns how many jobs were inserted.
    """
    if not settings.GITHUB_SETTLEMENT_ENABLED:
        return 0
    settlements = (
        (
            await db.execute(
                select(GitHubSettlement).where(
                    GitHubSettlement.state.in_(_RESUMABLE_SETTLEMENT_STATES)
                )
            )
        )
        .scalars()
        .all()
    )
    jobs = (
        (
            await db.execute(
                select(GitHubJob).where(
                    GitHubJob.kind == JobKind.ADVANCE_SETTLEMENT.value,
                    GitHubJob.status.in_(_OPEN_JOB_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    covered = _open_advance_targets(jobs)
    added = 0
    for settlement in settlements:
        if _text(settlement.state) not in _RESUMABLE_SETTLEMENT_STATES:
            continue
        if str(settlement.id) in covered:
            continue
        db.add(
            GitHubJob(
                id=uuid.uuid4(),
                kind=JobKind.ADVANCE_SETTLEMENT.value,
                payload={"settlement_id": str(settlement.id)},
                status=JobStatus.PENDING.value,
            )
        )
        added += 1
    if added:
        logger.info("queued %s pending GitHub settlement job(s)", added)
        await db.commit()
    return added


async def enqueue_job(
    db: AsyncSession,
    *,
    kind: str,
    payload: dict,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> GitHubJob | None:
    """Insert a pending job, or return None when an open job already holds ``dedupe_key``.

    Does not commit. The partial unique index ``uq_github_jobs_open_dedupe_key``
    covers the race on Postgres; the SELECT is what SQLite tests see.
    """
    if dedupe_key is not None:
        existing = await db.execute(
            select(GitHubJob).where(
                GitHubJob.dedupe_key == dedupe_key,
                GitHubJob.status.in_(_OPEN_JOB_STATUSES),
            )
        )
        if existing.scalars().first() is not None:
            return None
    job = GitHubJob(
        id=uuid.uuid4(),
        kind=_text(kind),
        payload=payload,
        dedupe_key=dedupe_key,
        status=JobStatus.PENDING.value,
        next_run_at=run_at if run_at is not None else datetime.now(timezone.utc),
    )
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        return None
    return job


async def cancel_jobs(db: AsyncSession, *, dedupe_key_prefix: str) -> int:
    """Mark open jobs whose key starts with the prefix cancelled.

    A running job finishes its current step and then sees the cancellation.
    Does not commit. ``%`` and ``_`` in the prefix match literally.
    """
    if not dedupe_key_prefix:
        raise ValueError("dedupe_key_prefix must not be empty")
    result = await db.execute(
        select(GitHubJob).where(
            GitHubJob.status.in_(_OPEN_JOB_STATUSES),
            GitHubJob.dedupe_key.startswith(dedupe_key_prefix, autoescape=True),
        )
    )
    jobs = result.scalars().all()
    for job in jobs:
        job.status = JobStatus.CANCELLED.value
        job.locked_until = None
        job.last_error = "cancelled"
    return len(jobs)


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
        try:
            async with async_session() as db:
                await expire_stale_leases(db)
        except Exception:
            logger.exception("GitHub lease expiry sweep failed")
        try:
            async with async_session() as db:
                await enqueue_pending_settlements(db)
        except Exception:
            logger.exception("GitHub pending settlement enqueue failed")
        await asyncio.sleep(settings.GITHUB_JOB_POLL_INTERVAL_S)
