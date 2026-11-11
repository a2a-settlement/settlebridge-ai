"""Release or forfeit fix-warranty holdbacks.

``run_once`` is one periodic pass. Nothing in ``main`` starts it. The process
scheduler (the contract loop polls every 60 seconds, or the GitHub job loop)
should call it with a session and injected sources::

    await run_once(
        db,
        now=datetime.now(timezone.utc),
        git_events_source=list_fix_reverts,  # (repo_full_name, fix_sha) -> commits
        advisory_source=list_followup_advisories,  # (repo_full_name, fix_sha) -> advisories
    )

A revert commit has ``message`` and ``linked_texts``. Classification uses
``verifier_client.classify_revert`` and falls back to ``unknown``; this module
does not implement the revert rule. An advisory has ``files`` and, when known,
``functions``.

Forfeit only for a security-motivated revert (``true``) or an overlapping
follow-up advisory. ``unknown`` parks the warranty for an operator and leaves
the escrow untouched. ``false`` releases the holdback. A window that ends with
no trigger releases it. State only moves forward, and a second pass is a no-op.

``decide_review`` is the operator decision for ``needs_review``. Callers must
be in ``PLATFORM_ADMIN_USER_IDS`` (empty means nobody).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.fix_verifier import FixVerification, FixWarranty, WarrantyState
from app.models.github import FunderLedgerEntry, GitHubLease, GitHubSettlement, LeaseStatus
from app.models.user import User
from app.services import exchange, verifier_client
from app.services.fix_warranty import (
    advisory_identity,
    advisory_overlaps,
    locations_of,
    verification_locations,
)

logger = logging.getLogger(__name__)

_ACTIVE = WarrantyState.ACTIVE.value
_NEEDS_REVIEW = WarrantyState.NEEDS_REVIEW.value
_RELEASED = WarrantyState.RELEASED.value
_FORFEITED = WarrantyState.FORFEITED.value
_FORWARD = {
    _ACTIVE: {_NEEDS_REVIEW, _RELEASED, _FORFEITED},
    _NEEDS_REVIEW: {_RELEASED, _FORFEITED},
}
_OPEN_ESCROW = frozenset({"held", "partially_released"})
_TEXT_LIMIT = 500


class ReviewError(ValueError):
    """The review decision was rejected. No warranty field was changed."""


class HoldbackFundsError(RuntimeError):
    """The holdback could not be moved, so the warranty state stays put."""


def classify_revert_outcome(message: str, linked_texts: list[str]) -> str:
    """Call the shared revert rule. Failure or any other label is ``unknown``.

    ``unknown`` is never turned into ``true`` here, so a missing classifier
    cannot forfeit a warranty.
    """
    classify = getattr(verifier_client, "classify_revert", None)
    if not callable(classify):
        return "unknown"
    try:
        result = classify(message, linked_texts)
    except Exception as exc:
        logger.error("revert classification failed (%s)", type(exc).__name__)
        return "unknown"
    if result in ("true", "false", "unknown"):
        return result
    return "unknown"


async def run_once(
    db: AsyncSession,
    *,
    now: datetime,
    git_events_source,
    advisory_source,
) -> int:
    """Advance every active warranty once. Returns how many changed state.

    A source that raises leaves that warranty active so a later pass can retry.
    Money is not released or forfeited on a failed lookup.
    """
    moment = _aware(now)
    # Ids only. A rollback expires loaded rows, and an async session cannot
    # refresh them lazily on the next iteration.
    result = await db.execute(select(FixWarranty.id).where(FixWarranty.state == _ACTIVE))
    warranty_ids = list(result.scalars().all())
    changed = 0
    for warranty_id in warranty_ids:
        try:
            warranty = await _warranty_by_id(db, warranty_id)
            if warranty is None or _text(warranty.state) != _ACTIVE:
                continue
            if await _process(
                db,
                warranty,
                now=moment,
                git_events_source=git_events_source,
                advisory_source=advisory_source,
            ):
                changed += 1
        except Exception:
            logger.exception("warranty %s was left active", warranty_id)
            await _rollback(db)
    return changed


async def decide_review(
    db: AsyncSession,
    warranty_id: uuid.UUID,
    *,
    decision: str,
    reviewer_id: uuid.UUID,
    now: datetime | None = None,
) -> FixWarranty:
    """Release or forfeit a warranty that is waiting on an operator.

    Records the reviewer and the decision. Escrow stays where it is until this
    call moves it. Any other state is rejected.
    """
    moment = _aware(now or datetime.now(timezone.utc))
    if decision not in ("release", "forfeit"):
        raise ReviewError("decision must be release or forfeit")
    if not _is_operator(reviewer_id):
        raise ReviewError("reviewer is not an operator")
    warranty = await _warranty_by_id(db, warranty_id)
    if warranty is None:
        raise ReviewError("warranty not found")
    if _text(warranty.state) != _NEEDS_REVIEW:
        raise ReviewError("warranty is not awaiting review")
    target = _RELEASED if decision == "release" else _FORFEITED
    if not _can_move(warranty.state, target):
        raise ReviewError("warranty state cannot move")
    action = "release" if decision == "release" else "refund"
    reason = (
        "operator released warranty holdback"
        if decision == "release"
        else "operator forfeited warranty holdback"
    )
    try:
        await _move_holdback(db, warranty, action=action, reason=reason)
    except Exception:
        await _rollback(db)
        raise
    warranty.state = target
    warranty.review_decision = decision
    warranty.reviewed_by_user_id = reviewer_id
    warranty.resolved_at = moment
    await db.commit()
    logger.info(
        "warranty %s review %s by %s",
        warranty.id,
        decision,
        reviewer_id,
    )
    return warranty


async def _process(
    db: AsyncSession,
    warranty: FixWarranty,
    *,
    now: datetime,
    git_events_source,
    advisory_source,
) -> bool:
    if _text(warranty.state) != _ACTIVE:
        return False
    verification = await _verification(db, warranty.verification_id)
    settlement = await _settlement(db, warranty.binding_id)
    repo = _repo_of(verification)
    fix_sha = _fix_sha_of(verification, settlement)
    if not repo or not fix_sha:
        logger.error("warranty %s has no repo or fix sha; escrow stays put", warranty.id)
        return False
    try:
        reverts = await _reverts(git_events_source, repo, fix_sha)
        advisories = await _advisories(advisory_source, repo, fix_sha)
    except Exception:
        logger.exception("warranty %s sources failed; escrow stays put", warranty.id)
        return False

    files, functions = (
        verification_locations(verification) if verification is not None else (set(), set())
    )
    security = next((item for item in reverts if item["classification"] == "true"), None)
    unknown = next((item for item in reverts if item["classification"] == "unknown"), None)
    benign = next((item for item in reverts if item["classification"] == "false"), None)
    overlap = _first_overlap(advisories, files, functions, warranty)

    if security is not None and _can_move(warranty.state, _FORFEITED):
        evidence = _revert_evidence(fix_sha, security)
        await _finish(
            db,
            warranty,
            target=_FORFEITED,
            action="refund",
            reason=f"security revert of fix {fix_sha}",
            trigger_kind="security_revert",
            evidence=evidence,
            now=now,
        )
        return True
    if overlap is not None and _can_move(warranty.state, _FORFEITED):
        await _finish(
            db,
            warranty,
            target=_FORFEITED,
            action="refund",
            reason=f"overlapping follow-up advisory {overlap['advisory_id']}",
            trigger_kind="followup_cve",
            evidence=overlap,
            now=now,
        )
        return True
    if unknown is not None and _can_move(warranty.state, _NEEDS_REVIEW):
        await _park_review(db, warranty, fix_sha=fix_sha, revert=unknown, now=now)
        return True
    if benign is not None and _can_move(warranty.state, _RELEASED):
        await _finish(
            db,
            warranty,
            target=_RELEASED,
            action="release",
            reason=f"non-security revert of fix {fix_sha}",
            trigger_kind="non_security_revert",
            evidence=_revert_evidence(fix_sha, benign),
            now=now,
        )
        return True
    if now >= _aware(warranty.window_ends_at) and _can_move(warranty.state, _RELEASED):
        await _finish(
            db,
            warranty,
            target=_RELEASED,
            action="release",
            reason="warranty window elapsed",
            trigger_kind="window_elapsed",
            evidence={"window_ends_at": _aware(warranty.window_ends_at).isoformat()},
            now=now,
        )
        return True
    return False


async def _finish(
    db: AsyncSession,
    warranty: FixWarranty,
    *,
    target: str,
    action: str,
    reason: str,
    trigger_kind: str,
    evidence: dict,
    now: datetime,
) -> None:
    if not _can_move(warranty.state, target):
        return
    await _move_holdback(db, warranty, action=action, reason=reason)
    warranty.state = target
    warranty.trigger_kind = trigger_kind
    warranty.trigger_evidence = evidence
    warranty.resolved_at = now
    await db.commit()
    logger.info("warranty %s %s via %s", warranty.id, target, trigger_kind)


async def _park_review(
    db: AsyncSession,
    warranty: FixWarranty,
    *,
    fix_sha: str,
    revert: dict,
    now: datetime,
) -> None:
    """Unknown revert: an operator decides, and the escrow is not touched."""
    if not _can_move(warranty.state, _NEEDS_REVIEW):
        return
    days = _setting_int("FIX_WARRANTY_REVIEW_SLA_DAYS", 7)
    warranty.state = _NEEDS_REVIEW
    warranty.trigger_kind = "unknown_revert"
    warranty.trigger_evidence = _revert_evidence(fix_sha, revert)
    warranty.review_due_at = now + timedelta(days=days)
    await db.commit()
    logger.info("warranty %s needs review; escrow held", warranty.id)


async def _move_holdback(
    db: AsyncSession,
    warranty: FixWarranty,
    *,
    action: str,
    reason: str,
) -> None:
    """Release the remainder to the payee, or refund it to the funder.

    ``release`` calls ``exchange.release_escrow``. ``refund`` calls
    ``exchange.refund_escrow``. An escrow that is already settled is left
    alone so a retry after a lost commit does not fail the state change.
    """
    funder, escrow_id, cancel_secret = await _funds(db, warranty)
    status = await _escrow_status(funder, escrow_id)
    if status is not None and status not in _OPEN_ESCROW:
        logger.info(
            "escrow %s already %s; warranty %s moves without a transfer",
            escrow_id,
            status,
            warranty.id,
        )
        return
    try:
        if action == "release":
            await asyncio.to_thread(
                exchange.release_escrow,
                funder,
                escrow_id,
                idempotency_key=f"warranty:{warranty.id}:release",
            )
        elif action == "refund":
            await asyncio.to_thread(
                exchange.refund_escrow,
                funder,
                escrow_id,
                reason,
                cancel_secret=cancel_secret,
            )
        else:
            raise HoldbackFundsError(f"unknown holdback action {action}")
    except Exception as exc:
        if _already_settled(exc):
            logger.info("holdback on escrow %s was already settled", escrow_id)
            return
        raise


async def _funds(db: AsyncSession, warranty: FixWarranty) -> tuple[User, str, str | None]:
    settlement = await _settlement(db, warranty.binding_id)
    escrow_id = getattr(settlement, "escrow_id", None) if settlement is not None else None
    if settlement is None or not isinstance(escrow_id, str) or not escrow_id:
        raise HoldbackFundsError(f"warranty {warranty.id} has no escrow")
    ledger = await _ledger(db, warranty.binding_id)
    funder_id = getattr(ledger, "funder_user_id", None) if ledger is not None else None
    funder = await _user(db, funder_id) if funder_id is not None else None
    if funder is None:
        raise HoldbackFundsError(f"warranty {warranty.id} has no funder")
    lease = await _settled_lease(db, warranty.binding_id)
    return funder, escrow_id, _cancel_secret(settlement, lease)


async def _reverts(source, repo: str, fix_sha: str) -> list[dict]:
    events = await _call_source(source, repo, fix_sha)
    found: list[dict] = []
    for event in events:
        parsed = _parse_revert(event, fix_sha)
        if parsed is None:
            continue
        sha, message, linked = parsed
        found.append(
            {
                "sha": sha,
                "message": message,
                "linked_texts": linked,
                "classification": classify_revert_outcome(message, linked),
            }
        )
    return found


async def _advisories(source, repo: str, fix_sha: str) -> list[object]:
    return await _call_source(source, repo, fix_sha)


def _first_overlap(
    advisories: list[object],
    files: set[str],
    functions: set[str],
    warranty: FixWarranty,
) -> dict | None:
    created = getattr(warranty, "created_at", None)
    start = _aware(created) if isinstance(created, datetime) else None
    end = _aware(warranty.window_ends_at)
    for advisory in advisories:
        if not isinstance(advisory, dict):
            continue
        if not _advisory_in_window(advisory, start, end):
            continue
        if not advisory_overlaps(files, functions, advisory):
            continue
        shared_files, shared_functions = _shared(files, functions, advisory)
        return {
            "advisory_id": advisory_identity(advisory),
            "files": sorted(shared_files),
            "functions": sorted(shared_functions),
        }
    return None


def _shared(files: set[str], functions: set[str], advisory: dict) -> tuple[set[str], set[str]]:
    adv_files, adv_functions = locations_of(advisory)
    shared_functions = functions & adv_functions if functions and adv_functions else set()
    return files & adv_files, shared_functions


def _advisory_in_window(advisory: dict, start: datetime | None, end: datetime) -> bool:
    published = advisory.get("published_at", advisory.get("published"))
    if published is None:
        return True
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return True
    if not isinstance(published, datetime):
        return True
    moment = _aware(published)
    if start is not None and moment < start:
        return False
    return moment <= end


def _parse_revert(event: object, fix_sha: str) -> tuple[str, str, list[str]] | None:
    if isinstance(event, dict):
        message = event.get("message")
        sha = event.get("sha") or event.get("commit") or ""
        linked = event.get("linked_texts")
        reverts = event.get("reverts", event.get("reverted_sha"))
    else:
        message = getattr(event, "message", None)
        sha = getattr(event, "sha", "") or ""
        linked = getattr(event, "linked_texts", None)
        reverts = getattr(event, "reverts", None)
    if isinstance(reverts, str) and reverts and reverts != fix_sha:
        return None
    if not isinstance(message, str):
        message = ""
    if not isinstance(sha, str):
        sha = ""
    return sha, message, _linked(linked)


def _linked(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, (list, tuple)):
        return []
    found: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
    return found


def _revert_evidence(fix_sha: str, revert: dict) -> dict:
    linked = [_clip(item) for item in revert.get("linked_texts") or []]
    return {
        "fix_sha": fix_sha,
        "revert_sha": _clip(str(revert.get("sha") or "")),
        "message": _clip(str(revert.get("message") or "")),
        "linked_texts": linked[:10],
        "classification": revert.get("classification"),
    }


async def _call_source(source, repo: str, fix_sha: str) -> list:
    if source is None:
        return []
    result = source(repo, fix_sha)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return []
    return list(result)


async def _escrow_status(funder: User, escrow_id: str) -> str | None:
    try:
        escrow = await asyncio.to_thread(exchange.get_escrow, funder, escrow_id)
    except Exception as exc:
        logger.warning("escrow status lookup failed (%s)", type(exc).__name__)
        return None
    if not isinstance(escrow, dict):
        return None
    status = escrow.get("status")
    if not isinstance(status, str) or not status.strip():
        return None
    return status.strip().lower()


def _already_settled(exc: BaseException) -> bool:
    code = _status_code(exc)
    if code == 400:
        return True
    text = str(exc).lower()
    return "already" in text and ("settled" in text or "released" in text or "refund" in text)


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _cancel_secret(settlement: GitHubSettlement, lease: GitHubLease | None) -> str | None:
    """Match settlement: an adopted lease hold uses the lease secret."""
    escrow_id = getattr(settlement, "escrow_id", None)
    hold_id = getattr(lease, "hold_escrow_id", None) if lease is not None else None
    if escrow_id and hold_id and str(escrow_id) == str(hold_id):
        secret = getattr(lease, "hold_cancel_secret", None)
        return secret if isinstance(secret, str) else None
    secret = getattr(settlement, "cancel_secret", None)
    return secret if isinstance(secret, str) else None


def _can_move(current: object, target: str) -> bool:
    return target in _FORWARD.get(_text(current), frozenset())


def _is_operator(reviewer_id: uuid.UUID | None) -> bool:
    if reviewer_id is None:
        return False
    raw = getattr(settings, "PLATFORM_ADMIN_USER_IDS", "") or ""
    allowed = {part.strip() for part in str(raw).split(",") if part.strip()}
    return str(reviewer_id) in allowed


def _setting_int(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _repo_of(verification: FixVerification | None) -> str:
    repo = getattr(verification, "repo_full_name", None)
    if isinstance(repo, str) and repo.strip():
        return repo.strip()
    return ""


def _fix_sha_of(verification: FixVerification | None, settlement: GitHubSettlement | None) -> str:
    sha = getattr(verification, "fix_sha", None) if verification is not None else None
    if isinstance(sha, str) and sha.strip():
        return sha.strip()
    sha = getattr(settlement, "head_sha", None) if settlement is not None else None
    if isinstance(sha, str) and sha.strip():
        return sha.strip()
    return ""


def _text(value: object) -> str:
    enumerated = getattr(value, "value", None)
    if isinstance(enumerated, str):
        return enumerated
    return str(value or "")


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _clip(value: str) -> str:
    if len(value) <= _TEXT_LIMIT:
        return value
    return value[: _TEXT_LIMIT - 3] + "..."


async def _rollback(db: AsyncSession) -> None:
    rollback = getattr(db, "rollback", None)
    if rollback is None:
        return
    result = rollback()
    if inspect.isawaitable(result):
        await result


async def _warranty_by_id(db: AsyncSession, warranty_id: uuid.UUID) -> FixWarranty | None:
    result = await db.execute(select(FixWarranty).where(FixWarranty.id == warranty_id))
    return _first(result)


async def _verification(db: AsyncSession, verification_id: uuid.UUID) -> FixVerification | None:
    result = await db.execute(
        select(FixVerification).where(FixVerification.id == verification_id)
    )
    return _first(result)


async def _settlement(db: AsyncSession, binding_id: uuid.UUID) -> GitHubSettlement | None:
    result = await db.execute(
        select(GitHubSettlement).where(GitHubSettlement.binding_id == binding_id)
    )
    return _first(result)


async def _ledger(db: AsyncSession, binding_id: uuid.UUID) -> FunderLedgerEntry | None:
    result = await db.execute(
        select(FunderLedgerEntry).where(FunderLedgerEntry.binding_id == binding_id)
    )
    return _first(result)


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return _first(result)


async def _settled_lease(db: AsyncSession, binding_id: uuid.UUID) -> GitHubLease | None:
    result = await db.execute(
        select(GitHubLease).where(
            GitHubLease.binding_id == binding_id,
            GitHubLease.status == LeaseStatus.SETTLED.value,
        )
    )
    return _first(result)


def _first(result: object):
    rows = list(result.scalars().all())
    return rows[0] if rows else None
