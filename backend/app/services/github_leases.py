"""Bounty leases: claim, renew, release, submit, and expiry."""

from __future__ import annotations

import asyncio
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

import httpx
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
from app.models.user import ACCOUNT_KIND_HOUSE, User
from app.services import exchange, github_app, github_bounty_service, trust_gateway
from app.services.github_errors import RetryableJobError
from app.services.repo_visibility import (
    bounty_public_clause,
    bounty_visible_clause,
    can_view_bounty,
    has_repo_read,
    hidden_bounty_ids,
    readable_private_repos,
)

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


def _listing_item(binding, bounty, spec, visibility: str | None = None) -> dict:
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
        "visibility": visibility,
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


async def fetch_advertised_bounties(
    db: AsyncSession,
    *,
    language: str | None,
    limit: int,
    readable_repos: set[str] | None = None,
):
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
            bounty_visible_clause(readable_repos)
            if readable_repos
            else bounty_public_clause(),
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


async def fetch_settled_lease(db: AsyncSession, binding_id: uuid.UUID):
    stmt = (
        select(GitHubLease)
        .where(
            GitHubLease.binding_id == binding_id,
            GitHubLease.status == LeaseStatus.SETTLED.value,
        )
        .limit(1)
    )
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


async def _listing_visibility(db: AsyncSession, repos: set[str]) -> dict[str, str | None]:
    if not repos:
        return {}
    stmt = select(GitHubRepoLink.repo_full_name, GitHubRepoLink.visibility).where(
        GitHubRepoLink.repo_full_name.in_(repos)
    )
    result = await db.execute(stmt)
    return {repo: visibility for repo, visibility in result.all()}


async def list_bounties(
    db: AsyncSession,
    *,
    language: str | None,
    limit: int,
    viewer=None,
) -> APIResult:
    readable = await readable_private_repos(db, viewer) if viewer is not None else None
    rows = await fetch_advertised_bounties(
        db,
        language=language,
        limit=_clamp_limit(limit),
        readable_repos=readable,
    )
    visibility = await _listing_visibility(
        db, {binding.repo_full_name for binding, _bounty, _spec in rows}
    )
    items = [
        _listing_item(binding, bounty, spec, visibility.get(binding.repo_full_name))
        for binding, bounty, spec in rows
    ]
    return APIResult(200, {"bounties": items})


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _client_error(exc: BaseException) -> bool:
    code = _status_code(exc)
    return code is not None and 400 <= code < 500


def _exception_text(exc: BaseException) -> str:
    parts = [str(exc)]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.text
        except Exception:
            body = ""
        if isinstance(body, str) and body:
            parts.append(body)
    return "\n".join(parts).lower()


HOLD_PENDING = "pending"


def pending_hold_marker(ttl_minutes: int) -> str:
    """Pending marker that keeps the TTL, so a replayed create sends the same request."""
    return f"{HOLD_PENDING}:{int(ttl_minutes)}"


def is_pending_hold(value) -> bool:
    return isinstance(value, str) and (
        value == HOLD_PENDING or value.startswith(HOLD_PENDING + ":")
    )


def _pending_ttl(value) -> int:
    _, _, raw = str(value).partition(":")
    return int(raw) if raw.isdigit() else _hold_ttl_minutes()


def _hold_ttl_minutes() -> int:
    """Cover the work cap, submitted hold, alert-fixed wait, and a margin."""
    return (
        int(settings.LEASE_HARD_CAP_MINUTES)
        + int(settings.LEASE_SUBMITTED_HOLD_HOURS) * 60
        + int(settings.GITHUB_ALERT_FIXED_TIMEOUT_HOURS) * 60
        + int(settings.GITHUB_HOLD_TTL_MARGIN_MINUTES)
    )


def enqueue_hold_refund(db, lease) -> None:
    """Queue a funder refund when a lease with a hold ends unpaid. Does not commit."""
    if not getattr(lease, "hold_escrow_id", None):
        return
    db.add(
        GitHubJob(
            id=uuid.uuid4(),
            kind=JobKind.REFUND_HOLD.value,
            payload={"lease_id": str(lease.id)},
            status=JobStatus.PENDING.value,
        )
    )


async def fetch_bounty_funder(db, bounty_id):
    """The bounty's requester. Settlement pays from this account, so holds use it too."""
    if bounty_id is None:
        return None
    bounty = await db.get(Bounty, bounty_id)
    requester_id = getattr(bounty, "requester_id", None) if bounty is not None else None
    if requester_id is None:
        return None
    return await db.get(User, requester_id)


class _PrincipalUnavailable(Exception):
    pass


_PRINCIPAL_MIN_CONFIDENCE = 0.5


async def _principal_links(
    client: httpx.AsyncClient, agent_id: str, api_key: str | None
) -> dict[str, float]:
    """Every principal linked to the account, with its confidence. Empty when none.

    Raises when the exchange can't say. A link without a confidence counts as certain.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = await client.get(
            f"{settings.effective_exchange_url}/v1/accounts/{agent_id}/principal",
            headers=headers,
            timeout=10.0,
        )
    except Exception as exc:
        raise _PrincipalUnavailable(type(exc).__name__) from exc
    if resp.status_code == 404:
        return {}
    if resp.status_code != 200:
        raise _PrincipalUnavailable(f"status {resp.status_code}")
    try:
        links = resp.json().get("links") or []
        found: dict[str, float] = {}
        for link in links:
            principal = str(link["principal_id"])
            raw = link.get("confidence")
            confidence = 1.0 if raw is None else float(raw)
            found[principal] = max(confidence, found.get(principal, 0.0))
        return found
    except Exception as exc:
        raise _PrincipalUnavailable("malformed principal response") from exc


async def _shares_principal(
    funder_agent: str,
    solver_agent: str,
    funder_key: str | None = None,
    solver_key: str | None = None,
) -> bool:
    """The exchange shows principal links only to the account itself or an operator,
    so each side is read with its own key; the gateway key is the fallback.
    Keys are credentials; never log them.
    """
    fallback = settings.GATEWAY_EXCHANGE_API_KEY or None
    async with httpx.AsyncClient() as client:
        funder_links = await _principal_links(client, funder_agent, funder_key or fallback)
        solver_links = await _principal_links(client, solver_agent, solver_key or fallback)
    return any(
        min(confidence, solver_links[principal]) >= _PRINCIPAL_MIN_CONFIDENCE
        for principal, confidence in funder_links.items()
        if principal in solver_links
    )


def _own_key(user, agent_id) -> str | None:
    """The user's exchange key, only when it belongs to ``agent_id``."""
    if str(getattr(user, "exchange_bot_id", None) or "") != str(agent_id):
        return None
    return getattr(user, "exchange_api_key", None) or None


async def _self_dealing_denial(
    db, binding, solver, github_user_id: int, claims
) -> APIResult | None:
    """403 when the solver is a funder by user, GitHub account, or exchange principal.

    Checks the bounty's requester and the repo link's funder. Fails closed: a
    principal lookup the exchange can't answer refuses the claim with 502.
    """
    if not settings.GITHUB_ENFORCE_KYA_LINEAGE_SEPARATION:
        return None
    funders = []
    bounty_funder = await fetch_bounty_funder(db, binding.bounty_id)
    if bounty_funder is not None:
        funders.append(bounty_funder)
    link = await fetch_repo_link(db, binding.repo_full_name)
    link_funder_id = getattr(link, "funder_user_id", None) if link is not None else None
    if link_funder_id is not None and all(f.id != link_funder_id for f in funders):
        if solver.id == link_funder_id:
            return _err(403, "self_dealing")
        link_funder = await db.get(User, link_funder_id)
        if link_funder is not None:
            funders.append(link_funder)
    if not funders:
        logger.info("bounty has no funder; refusing claim")
        return _err(409, "funder_missing")
    solver_agent = getattr(claims, "agent_id", None)
    for funder in funders:
        if solver.id == funder.id:
            return _err(403, "self_dealing")
        identity = await fetch_identity_for_user(db, funder.id)
        funder_github = None
        if identity is not None:
            funder_github = _positive_int(getattr(identity, "github_user_id", None))
        if funder_github is not None and funder_github == github_user_id:
            return _err(403, "self_dealing")
        funder_agent = getattr(funder, "exchange_bot_id", None)
        if not funder_agent or not solver_agent:
            continue
        if str(funder_agent) == str(solver_agent):
            return _err(403, "self_dealing")
        try:
            shared = await _shares_principal(
                str(funder_agent),
                str(solver_agent),
                getattr(funder, "exchange_api_key", None),
                _own_key(solver, solver_agent),
            )
        except _PrincipalUnavailable as exc:
            logger.info("principal lookup failed (%s); refusing claim", exc)
            return _err(502, "exchange_unavailable")
        if shared:
            return _err(403, "self_dealing")
    return None


async def _funder_for_hold(db, binding, amount_ate: int):
    """Balance-check the bounty funder. Returns ``(funder, None)`` or ``(None, error)``."""
    funder = await fetch_bounty_funder(db, binding.bounty_id)
    account_id = getattr(funder, "exchange_bot_id", None) if funder is not None else None
    if not account_id:
        logger.info("funder exchange account missing for lease hold")
        return None, _err(502, "exchange_unavailable")
    try:
        dashboard = await asyncio.to_thread(exchange.get_balance_dashboard, account_id)
    except Exception:
        logger.info("funder balance lookup failed")
        return None, _err(502, "exchange_unavailable")
    if not isinstance(dashboard, dict) or "available" not in dashboard:
        logger.info("funder balance lookup returned no available amount")
        return None, _err(502, "exchange_unavailable")
    available = dashboard["available"]
    if isinstance(available, bool) or not isinstance(available, (int, float)):
        logger.info("funder balance lookup returned a non-numeric available amount")
        return None, _err(502, "exchange_unavailable")
    if available < github_bounty_service.escrow_cost(int(amount_ate)):
        return None, _err(409, "funder_insufficient")
    return funder, None


class HoldRejected(Exception):
    """The exchange refused to create the hold; no escrow exists for it."""


async def resolve_hold(funder, lease, amount_ate: int) -> str:
    """Create, or recover, the lease's hold escrow and return its id.

    The request is deterministic per lease (the TTL comes from the pending
    marker), so a repeat replays the original (idempotency key) or names it
    (409 for an active task_id). Raises HoldRejected only on a 4xx other than
    409/429; anything else, including an unrecovered 409, propagates because an
    escrow may exist.
    """
    from app.services.github_settlement import _duplicate_escrow_id

    key = f"gh-hold:{lease.id}"
    marker = getattr(lease, "hold_escrow_id", None)
    ttl = _pending_ttl(marker) if is_pending_hold(marker) else _hold_ttl_minutes()
    # The stored secret makes a replay identical: same hash, task id, TTL, and key.
    secret = getattr(lease, "hold_cancel_secret", None)
    try:
        if secret:
            result = await asyncio.to_thread(
                exchange.create_locked_escrow,
                funder,
                provider_id=lease.agent_id,
                amount=int(amount_ate),
                task_id=key,
                ttl_minutes=ttl,
                idempotency_key=key,
                cancel_secret_hash=exchange.cancel_secret_sha256(secret),
            )
        else:
            result = await asyncio.to_thread(
                exchange.create_escrow,
                funder,
                provider_id=lease.agent_id,
                amount=int(amount_ate),
                task_id=key,
                ttl_minutes=ttl,
                idempotency_key=key,
            )
    except Exception as exc:
        recovered = _duplicate_escrow_id(exc)
        if recovered:
            return recovered
        if _client_error(exc) and _status_code(exc) not in (409, 429):
            raise HoldRejected(str(_status_code(exc))) from exc
        raise
    raw_id = result.get("escrow_id") if isinstance(result, dict) else None
    if not raw_id or not str(raw_id).strip():
        raise RuntimeError("create_escrow returned no escrow id")
    return str(raw_id).strip()


async def _lock_claim_hold(db, lease, funder, amount_ate: int) -> APIResult | None:
    """Create the hold for a lease committed with a pending ``hold_escrow_id`` marker.

    Only ``hold_escrow_id`` is written here, so a concurrent release is not
    overwritten; that release already queued a refund, which resolves the hold.
    """
    try:
        escrow_id = await resolve_hold(funder, lease, amount_ate)
    except HoldRejected:
        logger.info("hold escrow refused for lease %s", lease.id)
        lease.hold_escrow_id = None
        await _abort_hold(db, lease, refund=False)
        return _err(409, "funder_insufficient")
    except Exception:
        # The escrow may exist. Keep the pending marker so the refund job recovers it.
        logger.info("hold escrow create failed for lease %s", lease.id)
        await _abort_hold(db, lease, refund=True)
        return _err(502, "exchange_unavailable")
    lease.hold_escrow_id = escrow_id
    await db.commit()
    return None


async def _abort_hold(db, lease, *, refund: bool) -> None:
    lease.status = LeaseStatus.RELEASED.value
    lease.release_reason = "hold_failed"
    if refund:
        enqueue_hold_refund(db, lease)
    try:
        await db.commit()
    except Exception:
        logger.warning("could not persist hold_failed for lease %s", lease.id)
        try:
            await db.rollback()
        except Exception:
            logger.warning("rollback failed after hold_failed for lease %s", lease.id)


def _refund_is_final(exc: BaseException) -> bool:
    """True when the exchange says the escrow is already gone or in a terminal state."""
    code = _status_code(exc)
    if code is None or code < 400 or code >= 500:
        return False
    if code == 404:
        return True
    text = _exception_text(exc)
    return any(word in text for word in ("already", "refunded", "released", "expired", "disputed"))


def _reraise_refund(exc: BaseException) -> None:
    if _refund_is_final(exc):
        logger.info("hold refund found escrow already final")
        return
    logger.info("hold refund exchange error status=%s", _status_code(exc))
    raise RetryableJobError("exchange unavailable") from exc


async def _claim_caller(db: AsyncSession, claims):
    """``(user, github_user_id, error)``. ``error`` is an ``APIResult`` when the caller cannot claim."""
    users = await fetch_users_for_agent(db, claims.agent_id)
    if len(users) == 0:
        return None, None, _err(409, "unknown_agent_account")
    if len(users) > 1:
        return None, None, _err(409, "ambiguous_agent_account")
    user = users[0]
    identity = await fetch_identity_for_user(db, user.id)
    if identity is None:
        return None, None, _err(409, "no_github_identity")
    github_user_id = _positive_int(getattr(identity, "github_user_id", None))
    if github_user_id is None:
        return None, None, _err(409, "no_github_identity")
    return user, github_user_id, None


def _idempotency_key(value: object) -> tuple[str | None, APIResult | None]:
    """A missing key is unchanged. A present key is 1..128 printable ASCII."""
    if value is None:
        return None, None
    if (
        not isinstance(value, str)
        or len(value) < 1
        or len(value) > 128
        or any(ord(char) < 32 or ord(char) > 126 for char in value)
    ):
        return None, _err(400, "invalid_idempotency_key")
    return value, None


def _claim_body(binding, spec, lease, token: str, *, funds_reserved: bool) -> dict:
    body = {
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
    }
    if funds_reserved:
        body["funds_reserved"] = True
    return body


def _submit_success(lease) -> APIResult:
    return APIResult(
        200,
        {
            "lease_id": str(lease.id),
            "status": "submitted",
            "pr_number": lease.pr_number,
            "hold_expires_at": _iso(lease.hold_expires_at),
        },
    )


def _submit_trace(body: dict) -> str | None | APIResult:
    if "otel_trace_id" not in body or body.get("otel_trace_id") is None:
        return None
    trace = body.get("otel_trace_id")
    if not isinstance(trace, str):
        return _err(409, "idempotency_body_mismatch")
    return trace[:64]


def _submit_body_conflict(lease, binding, body) -> APIResult | None:
    """409 when a replay names a different pull request or trace than the one stored."""
    if not isinstance(body, dict):
        return _err(409, "idempotency_body_mismatch")
    pr_url = body.get("pr_url")
    parsed = parse_pr_url(pr_url) if isinstance(pr_url, str) else None
    if parsed is None:
        return _err(409, "idempotency_body_mismatch")
    repo, number = parsed
    if repo.casefold() != _text(binding.repo_full_name).casefold() or number != lease.pr_number:
        return _err(409, "idempotency_body_mismatch")
    trace = _submit_trace(body)
    if isinstance(trace, APIResult) or trace != lease.otel_trace_id:
        return _err(409, "idempotency_body_mismatch")
    return None


def _funds_reserved(lease) -> bool:
    marker = getattr(lease, "hold_escrow_id", None)
    return bool(marker) and not is_pending_hold(marker)


async def _lease_for_claim_key(db: AsyncSession, agent_id: str, key: str):
    stmt = (
        select(GitHubLease)
        .where(
            GitHubLease.agent_id == agent_id,
            GitHubLease.claim_idempotency_key == key,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _replay_claim(db: AsyncSession, lease, bounty_id: uuid.UUID) -> APIResult:
    binding = await fetch_binding(db, lease.binding_id)
    if binding is None or binding.bounty_id != bounty_id:
        return _err(409, "idempotency_key_reused")
    status = _text(lease.status)
    if status not in ACTIVE_LEASE_STATUSES:
        return _err(409, "idempotency_key_reused", status=status)
    spec = await fetch_spec(db, lease.spec_id)
    if spec is None:
        return _err(404, "not_found")
    locked = await _locked_lease(db, lease)
    if locked is None:
        return _err(409, "lease_not_active")
    status = _text(locked.status)
    if status not in ACTIVE_LEASE_STATUSES:
        await db.rollback()
        return _err(409, "idempotency_key_reused", status=status)
    # Only the hash was stored, so the lost response cannot be shown again.
    token = "sbl_" + secrets.token_urlsafe(32)
    locked.token_hash = hash_lease_token(token)
    await db.commit()
    return APIResult(
        200,
        _claim_body(binding, spec, locked, token, funds_reserved=_funds_reserved(locked)),
    )


async def _submit_replay(db, lease, key: str | None, body) -> APIResult | None:
    if key is None or getattr(lease, "submit_idempotency_key", None) != key:
        return None
    if _text(lease.status) != LeaseStatus.SUBMITTED.value:
        return None
    binding = await fetch_binding(db, lease.binding_id)
    if binding is None:
        return _err(404, "not_found")
    conflict = _submit_body_conflict(lease, binding, body)
    if conflict is not None:
        return conflict
    return _submit_success(lease)


async def claim_lease(
    db: AsyncSession,
    bounty_id: uuid.UUID,
    claims,
    idempotency_key: str | None = None,
) -> APIResult:
    key, key_error = _idempotency_key(idempotency_key)
    if key_error is not None:
        return key_error
    if key is not None:
        prior = await _lease_for_claim_key(db, claims.agent_id, key)
        if prior is not None:
            return await _replay_claim(db, prior, bounty_id)
    binding = await fetch_advertised_binding(db, bounty_id)
    if binding is None or binding.current_spec_id is None:
        return _err(404, "not_found")
    spec = await fetch_spec(db, binding.current_spec_id)
    if spec is None:
        return _err(404, "not_found")
    # A private bounty is 404 before trust or account errors, so a stranger
    # cannot tell it from a missing id. Public bounties still deny trust
    # before the account lookup.
    user = None
    github_user_id = None
    if await hidden_bounty_ids(db, [bounty_id]):
        user, github_user_id, early = await _claim_caller(db, claims)
        if early is not None:
            return early
        if not await can_view_bounty(db, bounty_id, user):
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
    if user is None:
        user, github_user_id, early = await _claim_caller(db, claims)
        if early is not None:
            return early
    denial_self = await _self_dealing_denial(db, binding, user, github_user_id, claims)
    if denial_self is not None:
        return denial_self
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
    # House solvers wait out the public claim window. Other checks above still apply.
    if getattr(user, "account_kind", None) == ACCOUNT_KIND_HOUSE:
        available_at = _aware(binding.created_at) + timedelta(
            hours=settings.GITHUB_HOUSE_CLAIM_DELAY_HOURS
        )
        if now < available_at:
            return _err(409, "house_claim_delay", available_at=_iso(available_at))
    # Insert first so the binding unique index picks the race winner before any
    # escrow exists. A losing claim rolls back and never locks the funder's funds.
    lock_funds = bool(settings.GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM)
    funder = None
    if lock_funds:
        funder, balance_error = await _funder_for_hold(db, binding, spec.amount_ate)
        if balance_error is not None:
            return balance_error
    hold_secret = None
    if lock_funds:
        hold_secret, _ = exchange.new_cancel_secret()
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
        claim_kya=claims.kya_level,
        claim_reputation=claims.reputation,
        hold_escrow_id=pending_hold_marker(_hold_ttl_minutes()) if lock_funds else None,
        hold_cancel_secret=hold_secret,
        claim_idempotency_key=key,
        created_at=now,
    )
    db.add(lease)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Two claims with one key can both miss the lookup. The unique index
        # keeps a single row; re-read it and answer as a replay.
        if key is not None:
            prior = await _lease_for_claim_key(db, claims.agent_id, key)
            if prior is not None:
                return await _replay_claim(db, prior, bounty_id)
        return _err(409, "lease_held")
    if lock_funds:
        hold_error = await _lock_claim_hold(db, lease, funder, spec.amount_ate)
        if hold_error is not None:
            return hold_error
    return APIResult(201, _claim_body(binding, spec, lease, token, funds_reserved=lock_funds))


def _lower(previous, current):
    if previous is None or current is None:
        return None
    return min(previous, current)


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
    enqueue_hold_refund(db, locked)
    await db.commit()
    return APIResult(200, {"lease_id": str(locked.id), "status": "released"})


async def submit_lease(
    db,
    lease_id: uuid.UUID,
    claims,
    lease_token: str | None,
    body,
    idempotency_key: str | None = None,
) -> APIResult:
    key, key_error = _idempotency_key(idempotency_key)
    if key_error is not None:
        return key_error
    lease, error = await _holder_lease(db, lease_id, claims, lease_token)
    if error is not None:
        return error
    replay = await _submit_replay(db, lease, key, body)
    if replay is not None:
        return replay
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
    if getattr(link, "visibility", None) in {"private", "internal"}:
        user_id = getattr(lease, "user_id", None)
        lease_user = await db.get(User, user_id) if user_id is not None else None
        if not await has_repo_read(db, lease_user, link):
            return _err(403, "repo_access_revoked")
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
    if locked is None:
        return _err(409, "lease_not_active")
    replay = await _submit_replay(db, locked, key, body)
    if replay is not None:
        await db.rollback()
        return replay
    if not _work_window_open(locked, now):
        await db.rollback()
        return _err(409, "lease_not_active")
    trace = body.get("otel_trace_id")
    locked.status = LeaseStatus.SUBMITTED.value
    locked.pr_number = pr_number
    locked.otel_trace_id = None if trace is None else trace[:64]
    locked.hold_expires_at = now + timedelta(hours=settings.LEASE_SUBMITTED_HOLD_HOURS)
    # Auto-merge reads these; keep the lower of the claim and submit values.
    locked.claim_kya = _lower(getattr(locked, "claim_kya", None), claims.kya_level)
    locked.claim_reputation = _lower(getattr(locked, "claim_reputation", None), claims.reputation)
    if key is not None:
        locked.submit_idempotency_key = key
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
    return _submit_success(locked)


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


async def bounty_status(db: AsyncSession, bounty_id: uuid.UUID, viewer=None) -> APIResult:
    binding = await fetch_binding_by_bounty(db, bounty_id)
    if binding is None:
        return _err(404, "not_found")
    if viewer is None:
        if await hidden_bounty_ids(db, [binding.bounty_id]):
            return _err(404, "not_found")
    elif not await can_view_bounty(db, bounty_id, viewer):
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
            enqueue_hold_refund(db, locked)
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


def _same_settlement_party(settlement, lease) -> bool:
    """True when the settlement was opened for this lease's spec and solver."""
    spec_id = getattr(settlement, "spec_id", None)
    solver = getattr(settlement, "solver_agent_id", None)
    if spec_id is None or solver is None:
        return False
    return spec_id == getattr(lease, "spec_id", None) and solver == getattr(lease, "agent_id", None)


def _hold_adopted_by(settlement, lease) -> bool:
    """True when the settlement's escrow is this lease's funder hold."""
    if settlement is None:
        return False
    escrow_id = getattr(settlement, "escrow_id", None)
    hold_id = getattr(lease, "hold_escrow_id", None)
    if not escrow_id or not hold_id:
        return False
    return str(escrow_id) == str(hold_id)


async def _resolve_submitted_hold(db, lease) -> None:
    """Settle a lease the settlement owns; otherwise expire it. Does not unbind.

    Settlement pays the newest settled lease and only adopts or refunds a hold
    before its escrow exists, so a party match settles the lease only while no
    escrow is set and no other lease is already settled for the binding.
    """
    settlement = await fetch_settlement(db, lease.binding_id)
    if _hold_adopted_by(settlement, lease):
        lease.status = LeaseStatus.SETTLED.value
        return
    if (
        settlement is not None
        and not getattr(settlement, "escrow_id", None)
        and _same_settlement_party(settlement, lease)
        and await fetch_settled_lease(db, lease.binding_id) is None
    ):
        lease.status = LeaseStatus.SETTLED.value
        return
    lease.status = LeaseStatus.EXPIRED.value
    lease.release_reason = "submitted_hold_elapsed"
    if not _hold_adopted_by(settlement, lease):
        enqueue_hold_refund(db, lease)


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
            binding = await fetch_binding(db, lease.binding_id)
            if binding is not None and _text(binding.state) in _SETTLEMENT_STATES:
                await _resolve_submitted_hold(db, lease)
                expired += 1
                continue
            if binding is not None:
                _unbind_binding(binding)
        lease.status = LeaseStatus.EXPIRED.value
        lease.release_reason = reason
        enqueue_hold_refund(db, lease)
        expired += 1
    await db.commit()
    if expired:
        logger.info("expired %s bounty leases", expired)
    return expired


async def handle_refund_hold(db: AsyncSession, job) -> None:
    """Refund ``payload["lease_id"]``'s hold escrow to the funder. Idempotent."""
    payload = getattr(job, "payload", None)
    if not isinstance(payload, dict):
        return
    raw_id = payload.get("lease_id")
    if not isinstance(raw_id, str):
        return
    try:
        lease_id = uuid.UUID(raw_id)
    except ValueError:
        return
    lease = await fetch_lease(db, lease_id)
    if lease is None or not getattr(lease, "hold_escrow_id", None):
        return
    if _text(lease.status) == LeaseStatus.SETTLED.value:
        return
    settlement = await fetch_settlement(db, lease.binding_id)
    adopted = getattr(settlement, "escrow_id", None) if settlement is not None else None
    if adopted and str(adopted) == str(lease.hold_escrow_id):
        return
    binding = await fetch_binding(db, lease.binding_id)
    funder = await fetch_bounty_funder(db, getattr(binding, "bounty_id", None))
    if funder is None:
        raise RetryableJobError("funder missing for hold refund")
    if is_pending_hold(lease.hold_escrow_id):
        spec = await fetch_spec(db, lease.spec_id)
        if spec is None:
            raise RetryableJobError("spec missing for hold refund")
        try:
            lease.hold_escrow_id = await resolve_hold(funder, lease, spec.amount_ate)
        except HoldRejected:
            lease.hold_escrow_id = None
            await db.commit()
            return
        except Exception as exc:
            raise RetryableJobError("exchange unavailable") from exc
        await db.commit()
    try:
        escrow = await asyncio.to_thread(exchange.get_escrow, funder, lease.hold_escrow_id)
    except Exception as exc:
        _reraise_refund(exc)
        return
    if not isinstance(escrow, dict):
        raise RetryableJobError("exchange unavailable")
    raw_status = escrow.get("status")
    if raw_status is None or raw_status == "":
        raise RetryableJobError("exchange unavailable")
    if str(raw_status).strip().lower() != "held":
        return
    try:
        await asyncio.to_thread(
            exchange.refund_escrow,
            funder,
            lease.hold_escrow_id,
            "lease ended",
            cancel_secret=getattr(lease, "hold_cancel_secret", None),
        )
    except Exception as exc:
        _reraise_refund(exc)
