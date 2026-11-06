"""Idempotent, resumable settlement of accepted GitHub bounties."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.bounty import Bounty, BountyStatus
from app.models.github import (
    AcceptanceSpec,
    BindingState,
    FunderLedgerEntry,
    GitHubBountyBinding,
    GitHubJob,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
    JobKind,
    JobStatus,
    LeaseStatus,
    LedgerStatus,
    SettlementState,
)
from app.models.user import User
from app.services import exchange, github_alerts, github_app, github_comments
from app.services.github_errors import PermanentJobError, RetryableJobError

logger = logging.getLogger(__name__)

# Exchange 409 detail: "An active escrow already exists for this task_id (escrow_id=<id>)".
_DUPLICATE_ESCROW_ID = re.compile(r"escrow_id=([a-fA-F0-9-]+)")
_FAILURE_STATUSES = frozenset({"refunded", "expired", "disputed"})
_SECRET_RE = re.compile(
    r"ate_[A-Za-z0-9_\-]+|Bearer\s+\S+|api[_-]?key[=:]\s*\S+",
    re.IGNORECASE,
)
_ERROR_LIMIT = 500
_MAX_TRANSITIONS = 6
_ALERT_POLL = timedelta(minutes=15)
_ALERT_MARKER = "<!-- settlebridge:alert-fixed -->"


@dataclass
class SettlementContext:
    settlement: GitHubSettlement
    binding: GitHubBountyBinding
    spec: AcceptanceSpec
    bounty: Bounty
    funder: User
    repo_link: GitHubRepoLink | None
    ledger: FunderLedgerEntry | None
    provider: User | None
    lease: GitHubLease | None


def next_action(state: str, escrow_status: str | None, delivered: bool | None) -> str:
    """Return the next transition: create, deliver, release, finalize, fail, or noop.

    ``failed`` and ``released`` are decided from local state. ``refunded``,
    ``expired``, and ``disputed`` fail only once an escrow exists. ``pending``
    ignores the exchange view. Delivery does not change status (it stays
    ``held``); ``delivered`` is that separate fact.
    """
    status = escrow_status.lower() if isinstance(escrow_status, str) and escrow_status else None
    if state == SettlementState.FAILED.value:
        return "noop"
    if state == SettlementState.RELEASED.value:
        return "finalize"
    if state in (SettlementState.ESCROW_CREATED.value, SettlementState.DELIVERED.value):
        if status in _FAILURE_STATUSES:
            return "fail"
    if state == SettlementState.PENDING.value:
        return "create"
    if state == SettlementState.ESCROW_CREATED.value:
        if status == "released" or delivered:
            return "release"
        return "deliver"
    if state == SettlementState.DELIVERED.value:
        return "release"
    return "noop"


def escrow_already_delivered(escrow: dict | None) -> bool:
    """True when get_escrow shows a delivery. Status stays ``held`` until release."""
    if not escrow:
        return False
    if escrow.get("delivered_at"):
        return True
    if escrow.get("delivered_content"):
        return True
    return bool(escrow.get("delivered_artifact_sha256"))


def delivery_content(
    binding: GitHubBountyBinding,
    settlement: GitHubSettlement,
    spec: AcceptanceSpec,
) -> str:
    payload: dict[str, object] = {
        "head_sha": settlement.head_sha,
        "repo": binding.repo_full_name,
        "spec_hash": spec.content_hash,
    }
    if binding.pr_number is not None:
        payload["pr_number"] = binding.pr_number
    digest = _assessment_diff_sha256(binding.last_assessment)
    if digest:
        payload["assessment_diff_sha256"] = digest
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


async def load_settlement_graph(db: AsyncSession, settlement_id: uuid.UUID) -> SettlementContext:
    settlement = await _scalar(
        db,
        select(GitHubSettlement).where(GitHubSettlement.id == settlement_id).with_for_update(),
    )
    if settlement is None:
        raise PermanentJobError(f"settlement {settlement_id} not found")

    binding = await _scalar(
        db, select(GitHubBountyBinding).where(GitHubBountyBinding.id == settlement.binding_id)
    )
    spec = await _scalar(db, select(AcceptanceSpec).where(AcceptanceSpec.id == settlement.spec_id))
    if binding is None or spec is None:
        await _fail_permanent(db, settlement, "binding or acceptance spec missing")

    bounty = await _scalar(db, select(Bounty).where(Bounty.id == binding.bounty_id))
    funder = None
    if bounty is not None:
        funder = await _scalar(db, select(User).where(User.id == bounty.requester_id))
    if bounty is None or funder is None:
        await _fail_permanent(db, settlement, "bounty or funder missing")

    repo_link = await _scalar(
        db, select(GitHubRepoLink).where(GitHubRepoLink.repo_full_name == binding.repo_full_name)
    )
    ledger = await _scalar(
        db, select(FunderLedgerEntry).where(FunderLedgerEntry.binding_id == binding.id)
    )
    # Pay the lease holder. exchange_bot_id is not unique, so the user comes from
    # the settled lease's user_id. solver_agent_id stays the exchange provider id.
    lease = await _scalar(
        db,
        select(GitHubLease)
        .where(
            GitHubLease.binding_id == settlement.binding_id,
            GitHubLease.status == LeaseStatus.SETTLED.value,
        )
        .order_by(GitHubLease.updated_at.desc(), GitHubLease.created_at.desc())
        .limit(1),
    )
    provider = None
    if lease is not None:
        provider = await _scalar(db, select(User).where(User.id == lease.user_id))
    return SettlementContext(
        settlement=settlement,
        binding=binding,
        spec=spec,
        bounty=bounty,
        funder=funder,
        repo_link=repo_link,
        ledger=ledger,
        provider=provider,
        lease=lease,
    )


async def advance_settlement(db: AsyncSession, settlement_id: uuid.UUID) -> GitHubSettlement:
    try:
        ctx = await load_settlement_graph(db, settlement_id)
    except PermanentJobError:
        raise
    except Exception as exc:
        raise RetryableJobError(_safe_error(exc)) from exc

    try:
        return await _advance(db, ctx)
    except (PermanentJobError, RetryableJobError):
        raise
    except Exception as exc:
        if _missing_exchange_account(exc):
            await _fail_permanent(db, ctx.settlement, str(exc))
        await _fail_retryable(db, ctx.settlement, exc)


async def _advance(db: AsyncSession, ctx: SettlementContext) -> GitHubSettlement:
    settlement = ctx.settlement
    state = _state_value(settlement)
    if state == SettlementState.FAILED.value:
        return settlement
    if state == SettlementState.RELEASED.value:
        await _finalize(db, ctx)
        return settlement
    if (
        not settings.GITHUB_SETTLEMENT_ENABLED
        or not settlement.solver_agent_id
        or ctx.lease is None
    ):
        return settlement

    for _ in range(_MAX_TRANSITIONS):
        state = _state_value(settlement)
        if state == SettlementState.FAILED.value:
            return settlement
        if state == SettlementState.RELEASED.value:
            await _finalize(db, ctx)
            return settlement

        escrow_status: str | None = None
        delivered: bool | None = None
        if state in (SettlementState.ESCROW_CREATED.value, SettlementState.DELIVERED.value):
            if not settlement.escrow_id:
                await _fail_permanent(db, settlement, "escrow id missing")
            escrow = await _read_escrow(ctx)
            escrow_status = _status_of(escrow)
            delivered = escrow_already_delivered(escrow)

        action = next_action(state, escrow_status, delivered)
        # Gate deliver and release: the solver can deliver on a hold before the merge.
        # DELIVERED is only reached after this gate passes.
        if (
            state == SettlementState.ESCROW_CREATED.value
            and action in ("deliver", "release")
            and _waits_for_alert(ctx)
        ):
            gate = await _alert_gate(db, ctx)
            if gate == "wait":
                await _schedule_recheck(db, settlement)
                return settlement
            if gate == "timeout":
                await _refund_unfixed(db, ctx)
        if action == "create":
            await _step_create(db, ctx)
            continue
        if action == "deliver":
            await _step_deliver(db, ctx)
            continue
        if action == "release" and state == SettlementState.ESCROW_CREATED.value:
            # Deliver step is already satisfied. Record it before release so a
            # crash cannot call deliver (which has no idempotency key).
            await _mark(db, settlement, SettlementState.DELIVERED.value)
            continue
        if action == "release":
            await _step_release(db, ctx, escrow_status)
            continue
        if action == "finalize":
            await _finalize(db, ctx)
            return settlement
        if action == "fail":
            await _fail_permanent(
                db, settlement, f"escrow {settlement.escrow_id} is {escrow_status}"
            )
        return settlement
    raise RuntimeError("settlement exceeded transition budget")


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _present(payload: dict, *keys: str):
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _plain_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            truncated = int(value)
        except (ValueError, OverflowError):
            return None
        if truncated != value:
            return None
        return truncated
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _hold_matches(escrow: dict, ctx: SettlementContext) -> bool:
    """Held, and provider/amount match when the exchange included those fields."""
    status = str(escrow.get("status") or "").strip().lower()
    if status != "held":
        return False
    provider = _present(escrow, "provider_id", "provider")
    amount = _present(escrow, "amount", "amount_ate")
    solver = ctx.settlement.solver_agent_id
    if provider is not None and str(provider) != str(solver):
        return False
    if amount is None:
        return True
    parsed = _plain_int(amount)
    return parsed is not None and parsed == int(ctx.spec.amount_ate)


async def _adopted_hold_id(ctx: SettlementContext) -> str | None:
    """Reuse the lease hold when it is still held for this solver and amount.

    A missing escrow (404) falls through to create. Any other lookup failure is
    re-raised so a blip cannot lock a second escrow while the hold is still open.
    """
    from app.services import github_leases

    lease = ctx.lease
    hold_id = getattr(lease, "hold_escrow_id", None) if lease is not None else None
    if not hold_id:
        return None
    if github_leases.is_pending_hold(hold_id):
        try:
            hold_id = await github_leases.resolve_hold(ctx.funder, lease, ctx.spec.amount_ate)
        except github_leases.HoldRejected:
            return None
        lease.hold_escrow_id = hold_id
    try:
        escrow = await asyncio.to_thread(exchange.get_escrow, ctx.funder, hold_id)
    except Exception as exc:
        if _status_code(exc) == 404:
            logger.info(
                "hold escrow %s is gone; settlement %s will create one",
                hold_id,
                ctx.settlement.id,
            )
            return None
        raise
    if not isinstance(escrow, dict) or not _hold_matches(escrow, ctx):
        logger.info("hold escrow %s not adoptable for settlement %s", hold_id, ctx.settlement.id)
        if isinstance(escrow, dict) and _status_of(escrow) == "held":
            await _refund_stale_hold(ctx, hold_id)
        return None
    return str(hold_id)


async def _refund_stale_hold(ctx: SettlementContext, hold_id: str) -> None:
    """The lease is SETTLED, so handle_refund_hold skips it; settlement returns it.

    A failed refund propagates (retryable) so a second escrow is never locked
    while this one is still held.
    """
    await asyncio.to_thread(
        exchange.refund_escrow, ctx.funder, hold_id, "hold not adoptable at settlement"
    )


async def _step_create(db: AsyncSession, ctx: SettlementContext) -> None:
    settlement = ctx.settlement
    # Deliver needs the solver's key; failing after create would strand funds in escrow.
    if ctx.provider is None or not getattr(ctx.provider, "exchange_api_key", None):
        raise ValueError("Solver has not linked an exchange account")
    # Create stays first. A later alert-fixed wait belongs after this step.
    hold_id = await _adopted_hold_id(ctx)
    if hold_id:
        settlement.escrow_id = hold_id
        await _mark(db, settlement, SettlementState.ESCROW_CREATED.value)
        return
    idempotency_key = f"{settlement.idempotency_prefix}:create"
    task_id = f"gh:{settlement.binding_id}:{settlement.spec_id}"
    try:
        result = await asyncio.to_thread(
            exchange.create_escrow,
            ctx.funder,
            provider_id=settlement.solver_agent_id,
            amount=ctx.spec.amount_ate,
            task_id=task_id,
            ttl_minutes=_create_ttl_minutes(ctx),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        if _missing_exchange_account(exc):
            raise
        recovered = _duplicate_escrow_id(exc)
        if recovered is None:
            raise
        logger.info("recovered duplicate escrow %s for settlement %s", recovered, settlement.id)
        result = {"escrow_id": recovered}

    escrow_id = _extract_escrow_id(result)
    if not escrow_id:
        raise RuntimeError("create_escrow returned no escrow id")
    settlement.escrow_id = escrow_id
    await _mark(db, settlement, SettlementState.ESCROW_CREATED.value)


def _create_ttl_minutes(ctx: SettlementContext) -> int:
    if not _waits_for_alert(ctx):
        return settings.ESCROW_TTL_MINUTES
    # The escrow must outlive the alert-fixed wait.
    wait = settings.GITHUB_ALERT_FIXED_TIMEOUT_HOURS * 60 + settings.GITHUB_HOLD_TTL_MARGIN_MINUTES
    return max(settings.ESCROW_TTL_MINUTES, wait)


def _waits_for_alert(ctx: SettlementContext) -> bool:
    return getattr(ctx.spec, "payment_trigger", None) == github_alerts.ALERT_FIXED_TRIGGER


async def _alert_gate(db: AsyncSession, ctx: SettlementContext) -> str:
    """Return ready, wait, or timeout. GitHub errors count as not fixed yet."""
    binding = ctx.binding
    dispatch = await github_alerts.dispatch_for_issue(
        db, binding.repo_full_name, binding.issue_number
    )
    state = None
    if dispatch is not None and ctx.repo_link is not None:
        kind = getattr(dispatch.alert_kind, "value", dispatch.alert_kind)
        fetch = (
            github_app.get_dependabot_alert
            if kind == "dependabot"
            else github_app.get_code_scanning_alert
        )
        try:
            alert = await fetch(
                ctx.repo_link.installation_id, binding.repo_full_name, dispatch.alert_number
            )
            state = alert.get("state") if isinstance(alert, dict) else None
        except Exception as exc:
            logger.warning(
                "alert lookup failed for settlement %s: %s", ctx.settlement.id, _safe_error(exc)
            )
        if isinstance(state, str) and state != dispatch.alert_state:
            dispatch.alert_state = state
            await db.commit()
    else:
        logger.warning("settlement %s waits for an alert with no dispatch row", ctx.settlement.id)
    if state == "fixed":
        return "ready"
    started = ctx.settlement.created_at or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    deadline = started + timedelta(hours=settings.GITHUB_ALERT_FIXED_TIMEOUT_HOURS)
    if datetime.now(timezone.utc) >= deadline:
        return "timeout"
    return "wait"


def _pending_advance_for_settlement(job: GitHubJob, settlement_id: str) -> bool:
    kind = getattr(job, "kind", None)
    if getattr(kind, "value", kind) != JobKind.ADVANCE_SETTLEMENT.value:
        return False
    status = getattr(job, "status", None)
    if getattr(status, "value", status) != JobStatus.PENDING.value:
        return False
    payload = getattr(job, "payload", None)
    if not isinstance(payload, dict):
        return False
    raw = payload.get("settlement_id")
    if isinstance(raw, uuid.UUID):
        return str(raw) == settlement_id
    return isinstance(raw, str) and raw == settlement_id


def _pull_next_run(job: GitHubJob, due: datetime) -> None:
    current = getattr(job, "next_run_at", None)
    if isinstance(current, datetime) and current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if not isinstance(current, datetime) or current > due:
        job.next_run_at = due


async def _schedule_recheck(db: AsyncSession, settlement: GitHubSettlement) -> None:
    """Queue one pending advance job so the alert is polled again.

    When a pending advance job for this settlement already exists, pull its
    ``next_run_at`` forward to at most one poll interval and leave that row in
    place. A pending job also stops ``enqueue_pending_settlements`` from adding
    another. The running job is the caller and is marked done after this returns,
    so only ``pending`` rows count.
    """
    due = datetime.now(timezone.utc) + _ALERT_POLL
    rows = await db.execute(
        select(GitHubJob).where(
            GitHubJob.kind == JobKind.ADVANCE_SETTLEMENT.value,
            GitHubJob.status == JobStatus.PENDING.value,
        )
    )
    pending = rows.scalars().all()
    target = str(settlement.id)
    matched = [job for job in pending if _pending_advance_for_settlement(job, target)]
    if matched:
        for job in matched:
            _pull_next_run(job, due)
    else:
        db.add(
            GitHubJob(
                id=uuid.uuid4(),
                kind=JobKind.ADVANCE_SETTLEMENT.value,
                payload={"settlement_id": target},
                status=JobStatus.PENDING.value,
                next_run_at=due,
            )
        )
    await db.commit()
    logger.info("settlement %s waiting for the alert to be fixed", settlement.id)


async def _refund_unfixed(db: AsyncSession, ctx: SettlementContext) -> NoReturn:
    settlement = ctx.settlement
    hours = settings.GITHUB_ALERT_FIXED_TIMEOUT_HOURS
    try:
        await asyncio.to_thread(
            exchange.refund_escrow,
            ctx.funder,
            settlement.escrow_id,
            f"alert not fixed within {hours}h of merge",
        )
    except Exception as exc:
        await _fail_retryable(db, settlement, exc)
    if ctx.repo_link is not None:
        try:
            await github_app.upsert_sticky_comment(
                ctx.repo_link.installation_id,
                ctx.binding.repo_full_name,
                ctx.binding.issue_number,
                _ALERT_MARKER,
                f"GitHub did not mark the security alert fixed within {hours} hours of the "
                "merge, so the bounty escrow was refunded to the funder.",
            )
        except Exception:
            logger.exception("alert timeout comment failed for settlement %s", settlement.id)
    await _fail_permanent(db, settlement, f"alert not fixed within {hours}h; escrow refunded")


async def _step_deliver(db: AsyncSession, ctx: SettlementContext) -> None:
    provider = ctx.provider
    # Only the provider may deliver. recreate_and_release posts /deliver with the
    # provider API key; the requester is rejected with 403.
    if provider is None or not getattr(provider, "exchange_api_key", None):
        raise ValueError("Solver has not linked an exchange account")
    content = delivery_content(ctx.binding, ctx.settlement, ctx.spec)
    await asyncio.to_thread(exchange.deliver, provider, ctx.settlement.escrow_id, content)
    await _mark(db, ctx.settlement, SettlementState.DELIVERED.value)


async def _step_release(
    db: AsyncSession, ctx: SettlementContext, escrow_status: str | None
) -> None:
    if (escrow_status or "").lower() != "released":
        await asyncio.to_thread(
            exchange.release_escrow,
            ctx.funder,
            ctx.settlement.escrow_id,
            idempotency_key=f"{ctx.settlement.idempotency_prefix}:release",
        )
    await _mark(db, ctx.settlement, SettlementState.RELEASED.value)


async def _finalize(db: AsyncSession, ctx: SettlementContext) -> None:
    ctx.binding.state = BindingState.PAID.value
    if ctx.ledger is None:
        logger.warning("no funder ledger for binding %s", ctx.binding.id)
    else:
        ctx.ledger.status = LedgerStatus.SETTLED.value
    ctx.bounty.status = BountyStatus.COMPLETED
    ctx.bounty.escrow_id = ctx.settlement.escrow_id
    if ctx.bounty.completed_at is None:
        ctx.bounty.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await _post_paid_comment(db, ctx)


async def _post_paid_comment(db: AsyncSession, ctx: SettlementContext) -> None:
    """Comment is best-effort. Paid rows are already committed."""
    if ctx.repo_link is None or not ctx.settlement.escrow_id:
        logger.error(
            "skipping paid comment for settlement %s (repo link or escrow id missing)",
            ctx.settlement.id,
        )
        return
    fee_ate = await _escrow_fee(ctx)
    try:
        marker = github_comments.bounty_marker(ctx.binding.bounty_id)
        body = github_comments.render_paid(
            bounty_id=ctx.binding.bounty_id,
            escrow_id=ctx.settlement.escrow_id,
            amount_ate=ctx.spec.amount_ate,
            fee_ate=fee_ate,
        )
        comment_id = await github_app.upsert_sticky_comment(
            ctx.repo_link.installation_id,
            ctx.binding.repo_full_name,
            ctx.binding.issue_number,
            marker,
            body,
            comment_id=ctx.binding.comment_id,
        )
    except Exception:
        logger.exception("paid comment failed for settlement %s", ctx.settlement.id)
        return
    if isinstance(comment_id, int) and ctx.binding.comment_id != comment_id:
        ctx.binding.comment_id = comment_id
        try:
            await db.commit()
        except Exception:
            logger.exception("failed to store paid comment id for settlement %s", ctx.settlement.id)


async def _escrow_fee(ctx: SettlementContext) -> int | None:
    """The fee the exchange actually charged, or None when it can't be read."""
    try:
        fee = (await _read_escrow(ctx)).get("fee_amount")
        return None if fee is None else int(fee)
    except Exception:
        logger.warning("could not read escrow fee for settlement %s", ctx.settlement.id)
        return None


async def _read_escrow(ctx: SettlementContext) -> dict:
    escrow = await asyncio.to_thread(exchange.get_escrow, ctx.funder, ctx.settlement.escrow_id)
    if not isinstance(escrow, dict):
        raise RuntimeError("escrow lookup returned no payload")
    return escrow


async def _mark(db: AsyncSession, settlement: GitHubSettlement, state: str) -> None:
    previous = _state_value(settlement)
    settlement.state = state
    settlement.last_error = None
    await db.commit()
    logger.info("settlement %s %s -> %s", settlement.id, previous, state)


async def _fail_permanent(db: AsyncSession, settlement: GitHubSettlement, message: str) -> NoReturn:
    safe = _safe_error(message)
    settlement.state = SettlementState.FAILED.value
    settlement.last_error = safe
    try:
        await db.commit()
    except Exception:
        logger.exception("could not persist failed settlement %s", settlement.id)
    raise PermanentJobError(safe)


async def _fail_retryable(
    db: AsyncSession, settlement: GitHubSettlement, exc: BaseException
) -> NoReturn:
    safe = _safe_error(exc)
    settlement.attempts = int(settlement.attempts or 0) + 1
    settlement.last_error = safe
    try:
        await db.commit()
    except Exception:
        logger.exception("could not persist retryable settlement error for %s", settlement.id)
    raise RetryableJobError(safe) from exc


async def _scalar(db: AsyncSession, stmt: object) -> object:
    return (await db.execute(stmt)).scalar_one_or_none()


def _state_value(settlement: GitHubSettlement) -> str:
    state = settlement.state
    if isinstance(state, SettlementState):
        return state.value
    return str(state)


def _status_of(escrow: dict) -> str | None:
    status = escrow.get("status")
    if status is None or status == "":
        return None
    return str(status)


def _extract_escrow_id(result: object) -> str:
    if not isinstance(result, dict):
        return ""
    raw = result.get("escrow_id") or result.get("id") or ""
    return str(raw).strip()


def _assessment_diff_sha256(assessment: object) -> str | None:
    if not isinstance(assessment, dict):
        return None
    for key in ("assessment_diff_sha256", "diff_sha256"):
        value = assessment.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _missing_exchange_account(exc: BaseException) -> bool:
    return isinstance(exc, ValueError) and "exchange account" in str(exc).lower()


def _duplicate_escrow_id(exc: BaseException) -> str | None:
    text = _error_text(exc)
    match = _DUPLICATE_ESCROW_ID.search(text)
    if match is None:
        return None
    lowered = text.lower()
    if (
        "409" in text
        or "already exists" in lowered
        or "duplicate" in lowered
        or "idempoten" in lowered
    ):
        return match.group(1)
    return None


def _error_text(exc: BaseException) -> str:
    parts = [str(exc)]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.text
        except Exception:
            body = None
        if isinstance(body, str) and body:
            parts.append(body)
    return "\n".join(parts)


def _safe_error(exc: BaseException | str) -> str:
    text = _SECRET_RE.sub("[redacted]", str(exc))
    text = " ".join(text.split())
    if len(text) > _ERROR_LIMIT:
        return text[: _ERROR_LIMIT - 3] + "..."
    return text
