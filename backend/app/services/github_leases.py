"""Bounty leases: claim, renew, release, submit, and expiry."""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, literal, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.config import settings
from app.models.bounty import Bounty, BountyStatus
from app.models.github import (
    ACTIVE_LEASE_STATUSES,
    AcceptanceSpec,
    BindingState,
    GitHubBountyBinding,
    GitHubIdentity,
    GitHubJob,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
    JobKind,
    JobStatus,
    LeaseStatus,
)
from app.models.user import User
from app.services import github_app, github_bounty_service, trust_gateway

logger = logging.getLogger(__name__)

_PR_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>[1-9]\d*)$"
)
_MAX_PR_NUMBER = 2_147_483_647
_HEARTBEAT_STATUSES = frozenset({"AVAILABLE_IDLE", "BUSY_PREEMPTED"})
_SETTLEMENT_STATES = frozenset(
    {
        BindingState.SETTLEMENT_PENDING.value,
        BindingState.PAID.value,
    }
)
_ADVERTISED_ON_UNBIND = frozenset(
    {
        BindingState.ADVERTISED.value,
        BindingState.EVALUATING.value,
        BindingState.NEEDS_REVIEW.value,
        BindingState.AWAITING_MERGE.value,
    }
)
_PRESENCE_OK = "ok"
_PRESENCE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class APIResult:
    status_code: int
    body: dict


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    text = _aware(value).isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _text(value: object) -> str:
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


def _err(status_code: int, error: str, **extra: object) -> APIResult:
    body: dict = {"error": error}
    body.update(extra)
    return APIResult(status_code, body)


def hash_lease_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_pr_url(url: str) -> tuple[str, int] | None:
    """Return ``(owner/repo, number)`` for a canonical GitHub pull URL."""
    if not isinstance(url, str):
        return None
    match = _PR_URL_RE.fullmatch(url.strip())
    if match is None:
        return None
    number = int(match.group("number"))
    if number > _MAX_PR_NUMBER:
        return None
    return f"{match.group('owner')}/{match.group('repo')}", number


def renewed_window(now: datetime, hard_cap: datetime) -> datetime:
    """Next work deadline, never past the lease hard cap."""
    now = _aware(now)
    hard_cap = _aware(hard_cap)
    proposed = now + timedelta(minutes=settings.LEASE_WORK_MINUTES)
    return min(proposed, hard_cap)


def cooldown_remaining(expired_timestamps: list[datetime], now: datetime) -> int | None:
    """Seconds until expired-lease hoarding drops below the cap, or None."""
    now = _aware(now)
    threshold = int(settings.LEASE_COOLDOWN_EXPIRIES)
    if threshold <= 0:
        return None
    window = timedelta(minutes=settings.LEASE_COOLDOWN_MINUTES)
    cutoff = now - window
    inside: list[datetime] = []
    for stamp in expired_timestamps:
        if stamp is None:
            continue
        aware = _aware(stamp)
        if aware >= cutoff:
            inside.append(aware)
    if len(inside) < threshold:
        return None
    inside.sort()
    # The expiry whose aging-out leaves fewer than `threshold` still in the window.
    pivot = inside[len(inside) - threshold]
    seconds = math.ceil(((pivot + window) - now).total_seconds())
    if seconds < 1:
        return 1
    return seconds


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return 1
    if limit > 200:
        return 200
    return limit


def _token_matches(stored_hash: str, presented: str | None) -> bool:
    if not presented or not stored_hash:
        return False
    digest = hash_lease_token(presented)
    if len(digest) != len(stored_hash):
        return False
    return hmac.compare_digest(digest, stored_hash)


def _issue_url(repo: str, issue_number: int) -> str:
    return f"https://github.com/{repo}/issues/{issue_number}"


def amount_usd_text(amount_ate: int) -> str:
    rate = Decimal(str(settings.GITHUB_ATE_PER_USD))
    if rate == 0:
        usd = Decimal(0)
    else:
        usd = Decimal(int(amount_ate)) / rate
    return github_bounty_service.format_usd(usd)


def spec_view(spec) -> dict:
    checks = spec.required_checks if spec.required_checks is not None else []
    policy = spec.policy if spec.policy is not None else {}
    return {
        "spec_id": str(spec.id),
        "version": spec.version,
        "content_hash": spec.content_hash,
        "test_command": spec.test_command,
        "required_checks": checks,
        "policy": policy,
        "payment_trigger": spec.payment_trigger,
        "required_kya": spec.required_kya,
        "required_ema": spec.required_ema,
    }


def _listing_item(binding, bounty, spec) -> dict:
    return {
        "bounty_id": str(binding.bounty_id),
        "repo": binding.repo_full_name,
        "issue_number": binding.issue_number,
        "issue_url": _issue_url(binding.repo_full_name, binding.issue_number),
        "title": bounty.title,
        "amount_ate": spec.amount_ate,
        "amount_usd": amount_usd_text(spec.amount_ate),
        "funding_status": "balance_checked_not_reserved",
        "spec": spec_view(spec),
    }


def _language_exists(language: str):
    wanted = language.strip().casefold()
    if not wanted:
        return None
    tag = func.unnest(Bounty.tags).column_valued("tag")
    return select(literal(1)).where(func.lower(tag) == wanted).exists()


def _clear_evaluation(binding) -> None:
    binding.evaluated_head_sha = None
    binding.evaluated_spec_id = None
    binding.last_assessment = None
    binding.review_cleared_by = None


def _unbind_binding(binding) -> None:
    binding.pr_number = None
    binding.lease_holder_agent_id = None
    _clear_evaluation(binding)
    if _text(binding.state) in _ADVERTISED_ON_UNBIND:
        binding.state = BindingState.ADVERTISED.value


def _expiry_reason(lease, now: datetime) -> str | None:
    status = _text(lease.status)
    now = _aware(now)
    if status == LeaseStatus.WORK.value:
        if _aware(lease.work_expires_at) < now or _aware(lease.hard_expires_at) < now:
            return "work_window_elapsed"
        return None
    if status == LeaseStatus.SUBMITTED.value:
        hold = lease.hold_expires_at
        if hold is not None and _aware(hold) < now:
            return "submitted_hold_elapsed"
        return None
    return None


def _work_window_open(lease, now: datetime) -> bool:
    if _text(lease.status) != LeaseStatus.WORK.value:
        return False
    now = _aware(now)
    if _aware(lease.work_expires_at) < now:
        return False
    if _aware(lease.hard_expires_at) < now:
        return False
    return True


def _renewable(lease, now: datetime) -> bool:
    """Work leases can be extended only while both deadlines are still ahead.

    ``work_expires_at`` may equal ``now`` (the sweeper uses a strict less-than).
    ``hard_expires_at`` must be strictly later: renewing at the cap cannot move it.
    """
    if _text(lease.status) != LeaseStatus.WORK.value:
        return False
    now = _aware(now)
    if _aware(lease.work_expires_at) < now:
        return False
    return now < _aware(lease.hard_expires_at)


def _assessment_view(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    findings = raw.get("findings")
    if not isinstance(findings, list):
        findings = []
    return {"outcome": raw.get("outcome"), "findings": findings}


def _positive_int(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def _pr_author_id(pull: dict) -> int | None:
    user = pull.get("user")
    if not isinstance(user, dict):
        return None
    return _positive_int(user.get("id"))


def _pr_opened_at(pull: dict) -> datetime | None:
    raw = pull.get("created_at")
    if not isinstance(raw, str):
        return None
    text_value = raw.strip()
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _pr_predates_lease(pull: dict, lease) -> bool:
    """Fail closed: a missing or unparseable PR timestamp is treated as too early."""
    opened = _pr_opened_at(pull) if isinstance(pull, dict) else None
    created = getattr(lease, "created_at", None)
    if opened is None or not isinstance(created, datetime):
        return True
    return opened < _aware(created)


def _stale_leases_stmt(now: datetime):
    work_past = and_(
        GitHubLease.status == LeaseStatus.WORK.value,
        or_(
            GitHubLease.work_expires_at < now,
            GitHubLease.hard_expires_at < now,
        ),
    )
    hold_past = and_(
        GitHubLease.status == LeaseStatus.SUBMITTED.value,
        GitHubLease.hold_expires_at.is_not(None),
        GitHubLease.hold_expires_at < now,
    )
    return select(GitHubLease).where(or_(work_past, hold_past)).with_for_update(skip_locked=True)


def _redis():
    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url(settings.REDIS_URL)


async def _store_presence(agent_id: str, record: dict) -> str:
    client = None
    presence = _PRESENCE_OK
    try:
        client = _redis()
        payload = json.dumps(record, separators=(",", ":"))
        await client.set(
            f"sb:worker:{agent_id}",
            payload,
            ex=int(settings.WORKER_HEARTBEAT_TTL_S),
        )
    except Exception:
        logger.warning("worker presence unavailable for %s", agent_id, exc_info=True)
        presence = _PRESENCE_UNAVAILABLE
    if client is not None:
        close = getattr(client, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logger.warning("redis client close failed", exc_info=True)
    return presence


async def fetch_advertised_bounties(db: AsyncSession, *, language: str | None, limit: int):
    # Bounty.category is lazy="joined"; noload keeps each row to the three selected entities.
    active = (
        select(GitHubLease.id)
        .where(
            GitHubLease.binding_id == GitHubBountyBinding.id,
            GitHubLease.status.in_(ACTIVE_LEASE_STATUSES),
        )
        .exists()
    )
    stmt = (
        select(GitHubBountyBinding, Bounty, AcceptanceSpec)
        .join(Bounty, Bounty.id == GitHubBountyBinding.bounty_id)
        .join(AcceptanceSpec, AcceptanceSpec.id == GitHubBountyBinding.current_spec_id)
        .options(noload(Bounty.category))
        .where(
            GitHubBountyBinding.state == BindingState.ADVERTISED.value,
            Bounty.status == BountyStatus.OPEN,
            ~active,
        )
        .order_by(GitHubBountyBinding.created_at, GitHubBountyBinding.id)
        .limit(limit)
    )
    if language:
        clause = _language_exists(language)
        if clause is not None:
            stmt = stmt.where(clause)
    result = await db.execute(stmt)
    return [(row[0], row[1], row[2]) for row in result.all()]


async def fetch_advertised_binding(db: AsyncSession, bounty_id: uuid.UUID):
    stmt = (
        select(GitHubBountyBinding)
        .where(
            GitHubBountyBinding.bounty_id == bounty_id,
            GitHubBountyBinding.state == BindingState.ADVERTISED.value,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_binding_by_bounty(db: AsyncSession, bounty_id: uuid.UUID):
    stmt = (
        select(GitHubBountyBinding)
        .where(GitHubBountyBinding.bounty_id == bounty_id)
        .order_by(GitHubBountyBinding.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_binding(db: AsyncSession, binding_id: uuid.UUID):
    return await db.get(GitHubBountyBinding, binding_id)


async def fetch_spec(db: AsyncSession, spec_id: uuid.UUID | None):
    if spec_id is None:
        return None
    return await db.get(AcceptanceSpec, spec_id)


async def fetch_users_for_agent(db: AsyncSession, agent_id: str) -> list:
    stmt = select(User).where(User.exchange_bot_id == agent_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def fetch_identity_for_user(db: AsyncSession, user_id: uuid.UUID):
    stmt = select(GitHubIdentity).where(GitHubIdentity.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def count_active_leases(db: AsyncSession, agent_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(GitHubLease)
        .where(
            GitHubLease.agent_id == agent_id,
            GitHubLease.status.in_(ACTIVE_LEASE_STATUSES),
        )
    )
    result = await db.execute(stmt)
    value = result.scalar_one()
    return int(value or 0)


async def fetch_expired_timestamps(db: AsyncSession, agent_id: str, since: datetime) -> list:
    stmt = select(GitHubLease.updated_at).where(
        GitHubLease.agent_id == agent_id,
        GitHubLease.status == LeaseStatus.EXPIRED.value,
        GitHubLease.updated_at >= since,
    )
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]


async def fetch_active_lease_for_binding(db: AsyncSession, binding_id: uuid.UUID):
    stmt = (
        select(GitHubLease)
        .where(
            GitHubLease.binding_id == binding_id,
            GitHubLease.status.in_(ACTIVE_LEASE_STATUSES),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_lease(db: AsyncSession, lease_id: uuid.UUID):
    return await db.get(GitHubLease, lease_id)


def _lease_for_update_stmt(lease_id: uuid.UUID):
    # populate_existing: the holder check may already have loaded this row.
    # READ COMMITTED shows a concurrent sweep only if that copy is refreshed.
    return (
        select(GitHubLease)
        .where(GitHubLease.id == lease_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def lock_lease(db: AsyncSession, lease_id: uuid.UUID):
    result = await db.execute(_lease_for_update_stmt(lease_id))
    return result.scalar_one_or_none()


async def lock_agent_for_claim(db: AsyncSession, agent_id: str) -> None:
    """Hold a transaction advisory lock so this agent's cap check and insert stay atomic."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:agent_id, 0))"),
        {"agent_id": agent_id},
    )


async def fetch_repo_link(db: AsyncSession, repo_full_name: str):
    stmt = select(GitHubRepoLink).where(
        func.lower(GitHubRepoLink.repo_full_name) == repo_full_name.casefold()
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_other_pr_binding(
    db: AsyncSession, repo_full_name: str, pr_number: int, binding_id: uuid.UUID
):
    stmt = (
        select(GitHubBountyBinding)
        .where(
            func.lower(GitHubBountyBinding.repo_full_name) == repo_full_name.casefold(),
            GitHubBountyBinding.pr_number == pr_number,
            GitHubBountyBinding.id != binding_id,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_settlement(db: AsyncSession, binding_id: uuid.UUID):
    stmt = select(GitHubSettlement).where(GitHubSettlement.binding_id == binding_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_agent_work_leases(db: AsyncSession, agent_id: str) -> list:
    stmt = (
        select(GitHubLease)
        .where(
            GitHubLease.agent_id == agent_id,
            GitHubLease.status == LeaseStatus.WORK.value,
        )
        .order_by(GitHubLease.created_at)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _holder_lease(
    db, lease_id, claims, lease_token
) -> tuple[object | None, APIResult | None]:
    lease = await fetch_lease(db, lease_id)
    if lease is None:
        return None, _err(404, "not_found")
    if claims.agent_id != lease.agent_id or not _token_matches(lease.token_hash, lease_token):
        return None, _err(403, "not_lease_holder")
    return lease, None


async def list_bounties(db: AsyncSession, *, language: str | None, limit: int) -> APIResult:
    rows = await fetch_advertised_bounties(db, language=language, limit=_clamp_limit(limit))
    items = [_listing_item(binding, bounty, spec) for binding, bounty, spec in rows]
    return APIResult(200, {"bounties": items})


async def claim_lease(db: AsyncSession, bounty_id: uuid.UUID, claims) -> APIResult:
    binding = await fetch_advertised_binding(db, bounty_id)
    if binding is None or binding.current_spec_id is None:
        return _err(404, "not_found")
    spec = await fetch_spec(db, binding.current_spec_id)
    if spec is None:
        return _err(404, "not_found")
    denial = trust_gateway.trust_denial(
        claims,
        required_kya=spec.required_kya,
        required_ema=spec.required_ema,
    )
    if denial:
        if isinstance(denial, dict):
            return APIResult(403, denial)
        return _err(403, "trust_insufficient")
    users = await fetch_users_for_agent(db, claims.agent_id)
    if len(users) == 0:
        return _err(409, "unknown_agent_account")
    if len(users) > 1:
        return _err(409, "ambiguous_agent_account")
    user = users[0]
    identity = await fetch_identity_for_user(db, user.id)
    if identity is None:
        return _err(409, "no_github_identity")
    github_user_id = _positive_int(getattr(identity, "github_user_id", None))
    if github_user_id is None:
        return _err(409, "no_github_identity")
    await lock_agent_for_claim(db, claims.agent_id)
    active = await count_active_leases(db, claims.agent_id)
    if active >= settings.LEASE_MAX_ACTIVE_PER_AGENT:
        return _err(409, "lease_limit")
    now = _now()
    since = now - timedelta(minutes=settings.LEASE_COOLDOWN_MINUTES)
    stamps = await fetch_expired_timestamps(db, claims.agent_id, since)
    retry_after = cooldown_remaining(stamps, now)
    if retry_after is not None:
        return _err(429, "cooldown", retry_after_s=retry_after)
    held = await fetch_active_lease_for_binding(db, binding.id)
    if held is not None:
        return _err(409, "lease_held")
    token = "sbl_" + secrets.token_urlsafe(32)
    lease = GitHubLease(
        id=uuid.uuid4(),
        binding_id=binding.id,
        agent_id=claims.agent_id,
        user_id=user.id,
        github_user_id=github_user_id,
        spec_id=binding.current_spec_id,
        token_hash=hash_lease_token(token),
        status=LeaseStatus.WORK.value,
        work_expires_at=now + timedelta(minutes=settings.LEASE_WORK_MINUTES),
        hard_expires_at=now + timedelta(minutes=settings.LEASE_HARD_CAP_MINUTES),
        created_at=now,
    )
    db.add(lease)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return _err(409, "lease_held")
    return APIResult(
        201,
        {
            "lease_id": str(lease.id),
            "lease_token": token,
            "bounty_id": str(binding.bounty_id),
            "repo": binding.repo_full_name,
            "issue_number": binding.issue_number,
            "issue_url": _issue_url(binding.repo_full_name, binding.issue_number),
            "spec": spec_view(spec),
            "test_command": spec.test_command,
            "work_expires_at": _iso(lease.work_expires_at),
            "hard_expires_at": _iso(lease.hard_expires_at),
        },
    )


async def _locked_lease(db, lease):
    locked = await lock_lease(db, lease.id)
    if locked is None:
        await db.rollback()
    return locked


async def renew_lease(db, lease_id: uuid.UUID, claims, lease_token: str | None) -> APIResult:
    lease, error = await _holder_lease(db, lease_id, claims, lease_token)
    if error is not None:
        return error
    now = _now()
    locked = await _locked_lease(db, lease)
    if locked is None or not _renewable(locked, now):
        if locked is not None:
            await db.rollback()
        return _err(409, "lease_not_active")
    locked.work_expires_at = renewed_window(now, locked.hard_expires_at)
    await db.commit()
    return APIResult(
        200,
        {
            "lease_id": str(locked.id),
            "work_expires_at": _iso(locked.work_expires_at),
            "hard_expires_at": _iso(locked.hard_expires_at),
        },
    )


async def release_lease(db, lease_id: uuid.UUID, claims, lease_token: str | None) -> APIResult:
    lease, error = await _holder_lease(db, lease_id, claims, lease_token)
    if error is not None:
        return error
    locked = await _locked_lease(db, lease)
    if locked is None:
        return _err(409, "lease_not_active")
    status = _text(locked.status)
    if status == LeaseStatus.SUBMITTED.value:
        await db.rollback()
        return _err(409, "lease_submitted")
    if status != LeaseStatus.WORK.value:
        await db.rollback()
        return _err(409, "lease_not_active")
    locked.status = LeaseStatus.RELEASED.value
    locked.release_reason = "agent_released"
    await db.commit()
    return APIResult(200, {"lease_id": str(locked.id), "status": "released"})


async def submit_lease(db, lease_id: uuid.UUID, claims, lease_token: str | None, body) -> APIResult:
    lease, error = await _holder_lease(db, lease_id, claims, lease_token)
    if error is not None:
        return error
    now = _now()
    if not _work_window_open(lease, now):
        return _err(409, "lease_not_active")
    if not isinstance(body, dict):
        return _err(422, "pr_wrong_repo")
    pr_url = body.get("pr_url")
    if not isinstance(pr_url, str):
        return _err(422, "pr_wrong_repo")
    if "otel_trace_id" in body and body.get("otel_trace_id") is not None:
        if not isinstance(body.get("otel_trace_id"), str):
            return _err(422, "invalid_body")
    parsed = parse_pr_url(pr_url)
    binding = await fetch_binding(db, lease.binding_id)
    if binding is None:
        return _err(404, "not_found")
    if parsed is None or parsed[0].casefold() != _text(binding.repo_full_name).casefold():
        return _err(422, "pr_wrong_repo")
    _repo, pr_number = parsed
    link = await fetch_repo_link(db, binding.repo_full_name)
    if link is None or getattr(link, "installation_id", None) is None:
        logger.warning("no GitHub installation for %s", binding.repo_full_name)
        return _err(502, "github_unavailable")
    try:
        pull = await github_app.get_pull_request(
            link.installation_id, binding.repo_full_name, pr_number
        )
    except github_app.GitHubAPIError:
        logger.warning("GitHub pull request lookup failed", exc_info=True)
        return _err(502, "github_unavailable")
    if not isinstance(pull, dict) or pull.get("state") != "open":
        return _err(422, "pr_not_open")
    body_text = pull.get("body") if isinstance(pull.get("body"), str) else None
    referenced = github_bounty_service.parse_closing_reference(body_text, binding.repo_full_name)
    if referenced != binding.issue_number:
        return _err(422, "pr_not_referencing_issue")
    if _pr_predates_lease(pull, lease):
        return _err(422, "pr_predates_lease")
    author = _pr_author_id(pull)
    expected = _positive_int(getattr(lease, "github_user_id", None))
    if author is None or expected is None or author != expected:
        return _err(422, "pr_author_mismatch")
    other = await fetch_other_pr_binding(db, binding.repo_full_name, pr_number, binding.id)
    if other is not None:
        return _err(409, "pr_already_bound")
    now = _now()
    locked = await _locked_lease(db, lease)
    if locked is None or not _work_window_open(locked, now):
        if locked is not None:
            await db.rollback()
        return _err(409, "lease_not_active")
    trace = body.get("otel_trace_id")
    locked.status = LeaseStatus.SUBMITTED.value
    locked.pr_number = pr_number
    locked.otel_trace_id = None if trace is None else trace[:64]
    locked.hold_expires_at = now + timedelta(hours=settings.LEASE_SUBMITTED_HOLD_HOURS)
    binding.pr_number = pr_number
    binding.lease_holder_agent_id = locked.agent_id
    _clear_evaluation(binding)
    head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
    head_sha = head.get("sha")
    if isinstance(head_sha, str) and head_sha:
        # CI may have finished before submit, when the check_suite webhook found no
        # lease. The handler re-reads checks and the diff from GitHub for this head.
        db.add(_evaluation_job(binding.repo_full_name, link.installation_id, pr_number, head_sha))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return _err(409, "pr_already_bound")
    return APIResult(
        200,
        {
            "lease_id": str(locked.id),
            "status": "submitted",
            "pr_number": pr_number,
            "hold_expires_at": _iso(locked.hold_expires_at),
        },
    )


def _evaluation_job(repo: str, installation_id: int, pr_number: int, head_sha: str) -> GitHubJob:
    return GitHubJob(
        id=uuid.uuid4(),
        kind=JobKind.CHECKS_COMPLETED.value,
        status=JobStatus.PENDING.value,
        payload={
            "event": "check_suite",
            "action": "completed",
            "data": {
                "check_suite": {"head_sha": head_sha, "pull_requests": [{"number": pr_number}]},
                "repository": {"full_name": repo},
                "installation": {"id": installation_id},
            },
        },
    )


async def bounty_status(db: AsyncSession, bounty_id: uuid.UUID) -> APIResult:
    binding = await fetch_binding_by_bounty(db, bounty_id)
    if binding is None:
        return _err(404, "not_found")
    settlement = await fetch_settlement(db, binding.id)
    lease = await fetch_active_lease_for_binding(db, binding.id)
    settlement_body = None
    if settlement is not None:
        settlement_body = {"state": _text(settlement.state)}
    lease_body = None
    if lease is not None:
        lease_body = {
            "active": _text(lease.status) in ACTIVE_LEASE_STATUSES,
            "status": _text(lease.status),
            "work_expires_at": _iso(lease.work_expires_at),
            "hold_expires_at": _iso(lease.hold_expires_at) if lease.hold_expires_at else None,
        }
    return APIResult(
        200,
        {
            "bounty_id": str(binding.bounty_id),
            "state": _text(binding.state),
            "pr_number": binding.pr_number,
            "evaluated_head_sha": binding.evaluated_head_sha,
            "assessment": _assessment_view(binding.last_assessment),
            "settlement": settlement_body,
            "lease": lease_body,
        },
    )


def _parse_heartbeat(body) -> dict | APIResult:
    if not isinstance(body, dict):
        return _err(422, "invalid_body")
    status = body.get("status")
    if status not in _HEARTBEAT_STATUSES:
        return _err(422, "invalid_body")
    capabilities = body.get("capabilities", None)
    if capabilities is not None:
        if not isinstance(capabilities, dict):
            return _err(422, "invalid_body")
        languages = capabilities.get("languages")
        if not isinstance(languages, list) or any(not isinstance(item, str) for item in languages):
            return _err(422, "invalid_body")
        capabilities = {"languages": list(languages)}
    min_usd = body.get("min_bounty_usd", None)
    if min_usd is not None and (isinstance(min_usd, bool) or not isinstance(min_usd, (int, float))):
        return _err(422, "invalid_body")
    max_tokens = body.get("max_tokens", None)
    if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int)):
        return _err(422, "invalid_body")
    return {
        "status": status,
        "capabilities": capabilities,
        "min_bounty_usd": min_usd,
        "max_tokens": max_tokens,
    }


async def heartbeat(db, claims, body) -> APIResult:
    parsed = _parse_heartbeat(body)
    if isinstance(parsed, APIResult):
        return parsed
    now = _now()
    leases = await fetch_agent_work_leases(db, claims.agent_id)
    locked_rows = []
    for lease in leases:
        locked = await lock_lease(db, lease.id)
        if locked is not None:
            locked_rows.append(locked)
    released: list[str] = []
    extended: list[str] = []
    lapsed: list[str] = []
    preempt = parsed["status"] == "BUSY_PREEMPTED"
    pending: list = []
    for locked in locked_rows:
        if _text(locked.status) != LeaseStatus.WORK.value:
            continue
        if preempt:
            pending.append(locked)
            continue
        # A lapsed lease is left for the expiry sweep; it must not be revived.
        if not _renewable(locked, now):
            lapsed.append(str(locked.id))
            continue
        pending.append(locked)
    for locked in pending:
        if preempt:
            locked.status = LeaseStatus.RELEASED.value
            locked.release_reason = "preempted"
            released.append(str(locked.id))
        else:
            locked.work_expires_at = renewed_window(now, locked.hard_expires_at)
            extended.append(str(locked.id))
    await db.commit()
    record = {
        "status": parsed["status"],
        "capabilities": parsed["capabilities"],
        "min_bounty_usd": parsed["min_bounty_usd"],
        "max_tokens": parsed["max_tokens"],
        "seen_at": _iso(now),
    }
    presence = await _store_presence(claims.agent_id, record)
    return APIResult(
        200,
        {
            "status": parsed["status"],
            "ttl_s": int(settings.WORKER_HEARTBEAT_TTL_S),
            "released_leases": released,
            "extended_leases": extended,
            "lapsed_leases": lapsed,
            "presence": presence,
        },
    )


async def expire_stale_leases(db: AsyncSession) -> int:
    """Expire work leases past their window and submitted holds past their hold. Commits."""
    now = _now()
    result = await db.execute(_stale_leases_stmt(now))
    expired = 0
    for lease in result.scalars().all():
        reason = _expiry_reason(lease, now)
        if reason is None:
            continue
        if reason == "submitted_hold_elapsed":
            binding = await db.get(GitHubBountyBinding, lease.binding_id)
            if binding is not None and _text(binding.state) in _SETTLEMENT_STATES:
                continue
            if binding is not None:
                _unbind_binding(binding)
        lease.status = LeaseStatus.EXPIRED.value
        lease.release_reason = reason
        expired += 1
    await db.commit()
    if expired:
        logger.info("expired %s bounty leases", expired)
    return expired
