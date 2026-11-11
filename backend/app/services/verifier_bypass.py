"""Verifier-bypass bounty reports.

Anyone may report that a fix the verifier marked verified is still exploitable.
Platform admins (``PLATFORM_ADMIN_USER_IDS``) triage and decide. An accepted
report stores ``reward_cents`` and becomes a corpus candidate. The reproducer
source stays with the submitter and those admins; corpus export keeps only a
hash.

``record_verifier_bypass_reward`` is the ledger payout hook. The coordinator
wires it to the ledger. This module does not move money.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.middleware.auth import is_platform_admin
from app.models.fix_verifier import (
    BypassState,
    FixVerdict,
    FixVerification,
    VerifierBypassSubmission,
)
from app.schemas.verifier_bypass import (
    DECISION_NOTES_MAX_LENGTH,
    FAILURE_CLASSES,
    NOTES_MAX_LENGTH,
    SUMMARY_MAX_LENGTH,
    TEST_SOURCE_MAX_LENGTH,
    ReproducerView,
    VerifierBypassCreate,
    VerifierBypassDecision,
    VerifierBypassView,
)
from app.utils.helpers import compute_content_hash

logger = logging.getLogger(__name__)

_OPEN_STATES = frozenset({BypassState.SUBMITTED.value, BypassState.TRIAGED.value})
_TERMINAL_DECISIONS = frozenset({BypassState.ACCEPTED.value, BypassState.REJECTED.value})


class BypassError(Exception):
    """A report or decision the API turns into ``{"error": code}``."""

    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def bounty_enabled() -> bool:
    return bool(settings.VERIFIER_BYPASS_BOUNTY_ENABLED)


def _as_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _identity_tokens(user: object) -> set[str]:
    """Stable ids for the signed-in submitter: user id and linked exchange account."""
    tokens: set[str] = set()
    user_id = getattr(user, "id", None)
    if user_id is not None:
        text = str(user_id).strip()
        if text:
            tokens.add(text.casefold())
        parsed = _as_uuid(user_id)
        if parsed is not None:
            tokens.add(str(parsed).casefold())
            tokens.add(parsed.hex)
    bot_id = getattr(user, "exchange_bot_id", None)
    if isinstance(bot_id, str) and bot_id.strip():
        tokens.add(bot_id.strip().casefold())
    return tokens


def submits_as_solver(verification: FixVerification, user: object) -> bool:
    """True when ``solver_principal_id`` is this submitter's user id or exchange account."""
    principal = verification.solver_principal_id
    if not isinstance(principal, str) or principal.strip() == "":
        return False
    if principal.strip().casefold() in _identity_tokens(user):
        return True
    parsed = _as_uuid(principal.strip())
    user_id = _as_uuid(getattr(user, "id", None))
    return parsed is not None and user_id is not None and parsed == user_id


def _require_enabled() -> None:
    if not bounty_enabled():
        raise BypassError(404, "not_found")


def _clean_summary(summary: str) -> str:
    if not isinstance(summary, str):
        raise BypassError(400, "invalid_summary")
    if len(summary) > SUMMARY_MAX_LENGTH:
        raise BypassError(400, "summary_too_large")
    cleaned = summary.strip()
    if cleaned == "":
        raise BypassError(400, "invalid_summary")
    return cleaned


def _clean_source(source: str) -> str:
    if not isinstance(source, str) or source.strip() == "":
        raise BypassError(400, "invalid_reproducer")
    if len(source) > TEST_SOURCE_MAX_LENGTH:
        raise BypassError(400, "reproducer_too_large")
    return source


def _failure_class(value: object) -> str:
    if not isinstance(value, str) or value not in FAILURE_CLASSES:
        raise BypassError(400, "invalid_failure_class")
    return value


def _reproducer_notes(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BypassError(400, "invalid_reproducer")
    if len(value) > NOTES_MAX_LENGTH:
        raise BypassError(400, "notes_too_large")
    return value


def _decision_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    if not isinstance(notes, str) or len(notes) > DECISION_NOTES_MAX_LENGTH:
        raise BypassError(400, "notes_too_large")
    return notes


async def _verification(db: AsyncSession, verification_id: uuid.UUID) -> FixVerification | None:
    statement = select(FixVerification).where(FixVerification.id == verification_id)
    return (await db.execute(statement)).scalar_one_or_none()


async def _submission(
    db: AsyncSession, submission_id: uuid.UUID
) -> VerifierBypassSubmission | None:
    statement = select(VerifierBypassSubmission).where(VerifierBypassSubmission.id == submission_id)
    return (await db.execute(statement)).scalar_one_or_none()


async def _open_submission(
    db: AsyncSession, verification_id: uuid.UUID, submitter_id: uuid.UUID
) -> VerifierBypassSubmission | None:
    statement = (
        select(VerifierBypassSubmission)
        .where(
            VerifierBypassSubmission.verification_id == verification_id,
            VerifierBypassSubmission.submitter_user_id == submitter_id,
            VerifierBypassSubmission.state.in_(_OPEN_STATES),
        )
        .limit(1)
    )
    return (await db.execute(statement)).scalars().first()


def _same_user(stored: object, user: object) -> bool:
    left = _as_uuid(stored)
    right = _as_uuid(getattr(user, "id", None))
    return left is not None and left == right


async def submit_bypass(
    db: AsyncSession, user: object, body: VerifierBypassCreate
) -> VerifierBypassSubmission:
    """Store one open report against a verified fix. The solver principal cannot report."""
    _require_enabled()
    submitter_id = _as_uuid(getattr(user, "id", None))
    if submitter_id is None:
        raise BypassError(403, "forbidden")
    verification = await _verification(db, body.verification_id)
    if verification is None:
        raise BypassError(404, "not_found")
    if verification.verdict != FixVerdict.VERIFIED.value:
        raise BypassError(409, "not_verified")
    if submits_as_solver(verification, user):
        raise BypassError(403, "solver_self_report")
    summary = _clean_summary(body.summary)
    source = _clean_source(body.reproducer.test_source)
    failure_class = _failure_class(body.reproducer.failure_class)
    notes = _reproducer_notes(body.reproducer.notes)
    if await _open_submission(db, verification.id, submitter_id) is not None:
        raise BypassError(409, "open_submission_exists")
    row = VerifierBypassSubmission(
        id=uuid.uuid4(),
        verification_id=verification.id,
        submitter_user_id=submitter_id,
        state=BypassState.SUBMITTED.value,
        summary=summary,
        reproducer={
            "test_source": source,
            "failure_class": failure_class,
            "notes": notes,
        },
        reward_cents=None,
        decision_notes=None,
        created_at=datetime.now(timezone.utc),
        decided_at=None,
    )
    db.add(row)
    await db.flush()
    return row


async def submission_for_viewer(
    db: AsyncSession, submission_id: uuid.UUID, user: object
) -> VerifierBypassSubmission | None:
    """The submitter or a platform admin may read the report. Anyone else gets a miss."""
    _require_enabled()
    row = await _submission(db, submission_id)
    if row is None:
        return None
    if _same_user(row.submitter_user_id, user) or is_platform_admin(user):
        return row
    return None


async def apply_decision(
    db: AsyncSession, submission_id: uuid.UUID, body: VerifierBypassDecision
) -> VerifierBypassSubmission:
    """Move submitted → triaged → accepted|rejected.

    Submitted may also go straight to accepted or rejected. Accepted and rejected
    set ``decided_at``. Accepted requires ``reward_cents`` >= 0 and calls
    ``record_verifier_bypass_reward``.
    """
    _require_enabled()
    decision = body.decision
    if decision == BypassState.ACCEPTED.value:
        if body.reward_cents is None or body.reward_cents < 0:
            raise BypassError(400, "reward_required")
    elif body.reward_cents is not None:
        raise BypassError(400, "reward_not_allowed")
    notes = _decision_notes(body.notes)
    row = await _submission(db, submission_id)
    if row is None:
        raise BypassError(404, "not_found")
    if decision == BypassState.TRIAGED.value:
        if row.state != BypassState.SUBMITTED.value:
            raise BypassError(409, "invalid_transition")
        row.state = BypassState.TRIAGED.value
    elif decision in _TERMINAL_DECISIONS:
        if row.state not in _OPEN_STATES:
            raise BypassError(409, "invalid_transition")
        row.state = decision
        row.decided_at = datetime.now(timezone.utc)
        if decision == BypassState.ACCEPTED.value:
            row.reward_cents = body.reward_cents
        else:
            row.reward_cents = None
    else:
        raise BypassError(400, "invalid_decision")
    if notes is not None:
        row.decision_notes = notes
    await db.flush()
    if decision == BypassState.ACCEPTED.value:
        await record_verifier_bypass_reward(db, row)
    return row


async def record_verifier_bypass_reward(
    db: AsyncSession, submission: VerifierBypassSubmission
) -> None:
    """Ledger payout hook for an accepted report.

    The coordinator wires this to the ledger. The hook is called in the same
    transaction as the acceptance, after ``reward_cents`` is stored. It writes
    no ledger row and does not call the exchange.
    """
    logger.info(
        "verifier bypass reward awaiting ledger wiring submission_id=%s reward_cents=%s",
        getattr(submission, "id", None),
        getattr(submission, "reward_cents", None),
    )
    return None


def serialize_bypass(row: VerifierBypassSubmission) -> dict:
    """Full report, including reproducer source. Callers must already be allowed to see it."""
    reproducer = row.reproducer if isinstance(row.reproducer, dict) else {}
    source = reproducer.get("test_source")
    failure_class = reproducer.get("failure_class")
    notes = reproducer.get("notes")
    view = VerifierBypassView(
        id=row.id,
        verification_id=row.verification_id,
        submitter_user_id=row.submitter_user_id,
        state=row.state,
        summary=row.summary,
        reproducer=ReproducerView(
            test_source=source if isinstance(source, str) else "",
            failure_class=failure_class if isinstance(failure_class, str) else "",
            notes=notes if isinstance(notes, str) else "",
        ),
        reward_cents=row.reward_cents,
        decision_notes=row.decision_notes,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )
    return view.model_dump(mode="json")


def _reproducer_hash(test_source: str) -> str:
    return "sha256:" + compute_content_hash(test_source)


async def export_corpus_candidates(db: AsyncSession) -> list[dict]:
    """Accepted reports for the corpus team. The reproducer source is not included."""
    statement = (
        select(VerifierBypassSubmission, FixVerification)
        .join(
            FixVerification,
            FixVerification.id == VerifierBypassSubmission.verification_id,
        )
        .where(VerifierBypassSubmission.state == BypassState.ACCEPTED.value)
        .order_by(VerifierBypassSubmission.created_at.asc(), VerifierBypassSubmission.id.asc())
    )
    found = []
    for submission, verification in (await db.execute(statement)).all():
        reproducer = submission.reproducer if isinstance(submission.reproducer, dict) else {}
        source = reproducer.get("test_source")
        failure_class = reproducer.get("failure_class")
        if not isinstance(source, str) or not isinstance(failure_class, str) or failure_class == "":
            logger.info(
                "skipping corpus candidate with an incomplete reproducer submission_id=%s",
                submission.id,
            )
            continue
        found.append(
            {
                "submission_id": str(submission.id),
                "verification_id": str(verification.id),
                "repo": verification.repo_full_name,
                "base_sha": verification.base_sha,
                "fix_sha": verification.fix_sha,
                "vuln_id": verification.vuln_id,
                "failure_class": failure_class,
                "reproducer_sha256": _reproducer_hash(source),
            }
        )
    return found
