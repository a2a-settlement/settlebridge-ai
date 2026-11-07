"""Challenge task state machine and job handlers (owner: P1-3).

Uses TASK_TRANSITIONS and CLAIM_TRANSITIONS from app.models.challenge and never moves
a task or claim outside them. Role and conflict rule: the challenger, the verifier,
and the adjudicator each have an operator different from the author and the solver,
and from each other.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeClaim, ChallengeTask
from app.models.github import GitHubJob
from app.schemas.challenge import ChallengeTaskCreate, ClaimIn


class InvalidTransition(Exception):
    pass


class RoleConflict(Exception):
    pass


async def create_task(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    installation_id: int,
    body: ChallengeTaskCreate,
    idempotency_key: str,
) -> ChallengeTask:
    raise NotImplementedError


async def freeze_contract(db: AsyncSession, task_id: uuid.UUID) -> ChallengeTask:
    """Insert the next challenge_contracts version with its content hash; move to frozen."""
    raise NotImplementedError


async def transition(
    db: AsyncSession, task: ChallengeTask, to_state: str, reason: str | None = None
) -> ChallengeTask:
    raise NotImplementedError


async def submit_claim(
    db: AsyncSession, *, task_id: uuid.UUID, attempt_id: uuid.UUID, claim: ClaimIn
) -> ChallengeClaim:
    raise NotImplementedError


async def handle_challenge_job(db: AsyncSession, job: GitHubJob) -> None:
    """Dispatch for JobKind.CHALLENGE_* kinds."""
    raise NotImplementedError


async def cancel_task(db: AsyncSession, task_id: uuid.UUID, reason: str) -> ChallengeTask:
    raise NotImplementedError
