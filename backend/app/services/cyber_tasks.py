"""Read-only SettleBridge Cyber task list. Public repos only."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bounty import Bounty, BountyStatus
from app.models.github import (
    ACTIVE_LEASE_STATUSES,
    AcceptanceSpec,
    BindingState,
    GitHubAlertDispatch,
    GitHubBountyBinding,
    GitHubLease,
    GitHubSettlement,
    LeaseStatus,
    SettlementState,
)
from app.services.github_leases import is_pending_hold
from app.services.ledger import _active_hold, _field, _value
from app.services.repo_visibility import bounty_public_clause

VIEWS = ("available", "in_progress", "completed")

_IN_PROGRESS_STATES = (
    BindingState.EVALUATING.value,
    BindingState.NEEDS_REVIEW.value,
    BindingState.AWAITING_MERGE.value,
    BindingState.SETTLEMENT_PENDING.value,
)
_CLOSED_STATES = (BindingState.PAID.value, BindingState.CANCELLED.value)
_NOT_PAID_SETTLEMENTS = frozenset({SettlementState.FAILED.value, "refunded"})
_PAYMENT_PENDING_SETTLEMENTS = frozenset(
    {
        SettlementState.PENDING.value,
        SettlementState.ESCROW_CREATED.value,
        SettlementState.DELIVERED.value,
    }
)
_DISPATCH_SOURCES = frozenset({"dependabot", "code_scanning"})


def funding_state(binding: Any, settlement: Any, leases: Any = ()) -> str:
    """Funder-facing money state for one Cyber task.

    Precedence follows the public contract. An active lease counts as funds held
    only when ``ledger._active_hold`` is true and the id is not a pending marker.
    """
    binding_state = _value(_field(binding, "state"))
    settlement_state = _value(_field(settlement, "state")) if settlement is not None else None
    if binding_state == BindingState.PAID.value and (
        settlement is None or settlement_state == SettlementState.RELEASED.value
    ):
        return "paid"
    if settlement_state in _NOT_PAID_SETTLEMENTS or binding_state == BindingState.CANCELLED.value:
        return "not_paid"
    if settlement_state == SettlementState.NEEDS_REVIEW.value:
        return "payment_review"
    if (
        settlement_state in _PAYMENT_PENDING_SETTLEMENTS
        or binding_state == BindingState.SETTLEMENT_PENDING.value
    ):
        return "payment_pending"
    if any(_real_hold(lease) for lease in leases or ()):
        return "funds_held"
    return "balance_checked"


def work_state(binding: Any, lease: Any = None) -> str:
    """Solver-facing progress. Binding state wins over an active lease."""
    state = _value(_field(binding, "state"))
    if state == BindingState.CANCELLED.value:
        return "cancelled"
    if state == BindingState.PAID.value:
        return "completed"
    if state == BindingState.SETTLEMENT_PENDING.value:
        return "settling"
    if state == BindingState.AWAITING_MERGE.value:
        return "awaiting_merge"
    if state == BindingState.NEEDS_REVIEW.value:
        return "needs_maintainer_review"
    if state == BindingState.EVALUATING.value:
        return "evaluating"
    if lease is not None:
        status = _value(_field(lease, "status"))
        if status == LeaseStatus.SUBMITTED.value:
            return "pr_submitted"
        if status == LeaseStatus.WORK.value:
            return "claimed"
    return "available"


def task_source(dispatch: Any) -> str:
    if dispatch is None:
        return "security_label"
    kind = _value(_field(dispatch, "alert_kind"))
    if kind in _DISPATCH_SOURCES:
        return kind
    return "security_label"


def _real_hold(lease: Any) -> bool:
    if is_pending_hold(_field(lease, "hold_escrow_id")):
        return False
    return _active_hold(lease)


def _dispatch_exists():
    return (
        select(literal(1))
        .where(
            GitHubAlertDispatch.repo_full_name == GitHubBountyBinding.repo_full_name,
            GitHubAlertDispatch.issue_number == GitHubBountyBinding.issue_number,
        )
        .correlate(GitHubBountyBinding)
        .exists()
    )


def _active_lease_exists():
    return (
        select(GitHubLease.id)
        .where(
            GitHubLease.binding_id == GitHubBountyBinding.id,
            GitHubLease.status.in_(ACTIVE_LEASE_STATUSES),
        )
        .correlate(GitHubBountyBinding)
        .exists()
    )


def _cyber_clause():
    """Dispatch-backed alert, or the binding's current spec is security tier."""
    return or_(_dispatch_exists(), AcceptanceSpec.security_tier.is_(True))


def _where(view: str) -> list:
    active = _active_lease_exists()
    clauses: list = [_cyber_clause(), bounty_public_clause()]
    if view == "available":
        clauses.extend(
            [
                GitHubBountyBinding.state == BindingState.ADVERTISED.value,
                Bounty.status == BountyStatus.OPEN,
                ~active,
            ]
        )
    elif view == "in_progress":
        clauses.extend(
            [
                or_(
                    GitHubBountyBinding.state.in_(_IN_PROGRESS_STATES),
                    active,
                ),
                GitHubBountyBinding.state.notin_(_CLOSED_STATES),
            ]
        )
    elif view == "completed":
        clauses.append(GitHubBountyBinding.state == BindingState.PAID.value)
    else:
        raise ValueError(view)
    return clauses


def _selectable(view: str, *columns: Any):
    return (
        select(*columns)
        .select_from(GitHubBountyBinding)
        .join(Bounty, Bounty.id == GitHubBountyBinding.bounty_id)
        .join(AcceptanceSpec, AcceptanceSpec.id == GitHubBountyBinding.current_spec_id)
        .where(*_where(view))
    )


def _count_statement(view: str):
    return _selectable(view, func.count(GitHubBountyBinding.id))


def _page_statement(view: str, page: int, page_size: int):
    offset = (page - 1) * page_size
    return (
        _selectable(view, GitHubBountyBinding, Bounty.title, AcceptanceSpec)
        .order_by(GitHubBountyBinding.updated_at.desc(), GitHubBountyBinding.id.desc())
        .offset(offset)
        .limit(page_size)
    )


def _iso(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _issue_url(repo: str, issue_number: int) -> str:
    return f"https://github.com/{repo}/issues/{issue_number}"


def _pr_url(repo: str, pr_number: int | None) -> str | None:
    if pr_number is None:
        return None
    return f"https://github.com/{repo}/pull/{pr_number}"


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        updated = value
    elif isinstance(value, str):
        try:
            updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    else:
        return datetime.min.replace(tzinfo=timezone.utc)
    if updated.tzinfo is None:
        return updated.replace(tzinfo=timezone.utc)
    return updated


def _when(row: Any) -> tuple:
    return (_as_datetime(_field(row, "updated_at")), str(_field(row, "id")))


def _index_dispatches(rows: list, wanted: set[tuple[str, int]]) -> dict[tuple[str, int], Any]:
    chosen: dict[tuple[str, int], Any] = {}
    for row in rows:
        issue_number = _field(row, "issue_number")
        if issue_number is None:
            continue
        key = (str(_field(row, "repo_full_name")), int(issue_number))
        if key not in wanted:
            continue
        current = chosen.get(key)
        if current is None or _when(row) > _when(current):
            chosen[key] = row
    return chosen


def _prefer_lease(rows: list) -> Any:
    chosen = None
    for row in rows:
        if chosen is None or _value(_field(row, "status")) == LeaseStatus.SUBMITTED.value:
            chosen = row
    return chosen


async def _load_dispatches(db: AsyncSession, pairs: set[tuple[str, int]]) -> dict:
    if not pairs:
        return {}
    repos = {repo for repo, _issue in pairs}
    issues = {issue for _repo, issue in pairs}
    stmt = select(GitHubAlertDispatch).where(
        GitHubAlertDispatch.repo_full_name.in_(repos),
        GitHubAlertDispatch.issue_number.in_(issues),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return _index_dispatches(list(rows), pairs)


async def _load_leases(db: AsyncSession, binding_ids: list[uuid.UUID]) -> dict:
    stmt = select(GitHubLease).where(
        GitHubLease.binding_id.in_(binding_ids),
        GitHubLease.status.in_(ACTIVE_LEASE_STATUSES),
    )
    grouped: dict[uuid.UUID, list] = {}
    for row in (await db.execute(stmt)).scalars().all():
        grouped.setdefault(row.binding_id, []).append(row)
    return grouped


async def _load_settlements(db: AsyncSession, binding_ids: list[uuid.UUID]) -> dict:
    stmt = select(GitHubSettlement).where(GitHubSettlement.binding_id.in_(binding_ids))
    rows = (await db.execute(stmt)).scalars().all()
    return {row.binding_id: row for row in rows}


def _item(binding, title, spec, dispatch, leases, settlement) -> dict[str, Any]:
    repo = str(binding.repo_full_name)
    issue_number = int(binding.issue_number)
    pr_number = None if binding.pr_number is None else int(binding.pr_number)
    return {
        "bounty_id": str(binding.bounty_id),
        "title": "" if title is None else str(title),
        "repo": repo,
        "issue_number": issue_number,
        "issue_url": _issue_url(repo, issue_number),
        "pr_number": pr_number,
        "pr_url": _pr_url(repo, pr_number),
        "source": task_source(dispatch),
        "reward_ate": int(spec.amount_ate),
        "funding_state": funding_state(binding, settlement, leases),
        "work_state": work_state(binding, _prefer_lease(leases)),
        "payment_trigger": str(spec.payment_trigger),
        "eligibility": {
            "min_reputation": float(spec.required_ema),
            "min_kya": int(spec.required_kya),
        },
        "created_at": _iso(binding.created_at),
        "updated_at": _iso(binding.updated_at),
    }


async def list_tasks(
    db: AsyncSession, *, view: str, page: int = 1, page_size: int = 12
) -> dict[str, Any]:
    if view not in VIEWS:
        raise ValueError(view)
    total = int((await db.execute(_count_statement(view))).scalar_one() or 0)
    payload: dict[str, Any] = {
        "view": view,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [],
    }
    if total == 0:
        return payload
    rows = (await db.execute(_page_statement(view, page, page_size))).all()
    if not rows:
        return payload
    bindings = [row[0] for row in rows]
    binding_ids = [binding.id for binding in bindings]
    pairs = {(str(binding.repo_full_name), int(binding.issue_number)) for binding in bindings}
    dispatches = await _load_dispatches(db, pairs)
    leases = await _load_leases(db, binding_ids)
    settlements = await _load_settlements(db, binding_ids)
    payload["items"] = [
        _item(
            binding,
            title,
            spec,
            dispatches.get((str(binding.repo_full_name), int(binding.issue_number))),
            leases.get(binding.id, ()),
            settlements.get(binding.id),
        )
        for binding, title, spec in rows
    ]
    return payload
