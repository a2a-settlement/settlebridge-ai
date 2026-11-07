"""Read-only list of the signed-in user's GitHub bounty leases."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github import (
    AcceptanceSpec,
    BindingState,
    GitHubBountyBinding,
    GitHubLease,
    GitHubSettlement,
    LeaseStatus,
    SettlementState,
)
from app.schemas.challenge import MyLeaseOut, MyLeasesOut

_MAX_LEASES = 100
# released and failed do not advance. needs_review can still be paid or failed.
_TERMINAL_SETTLEMENTS = frozenset({SettlementState.RELEASED.value, SettlementState.FAILED.value})
# Stored mediator outcomes are uppercase. Any other outcome is not an evaluation.
_EVALUATIONS: dict[str, Literal["acceptable", "needs_review"]] = {
    "ACCEPTABLE": "acceptable",
    "NEEDS_REVIEW": "needs_review",
}


def _as_dict(raw: object) -> dict | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return raw
    return None


def _iso_utc(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    rendered = value.isoformat()
    if rendered.endswith("+00:00"):
        return rendered[:-6] + "Z"
    return rendered


def _evaluation(raw: object) -> Literal["acceptable", "needs_review"] | None:
    assessment = _as_dict(raw)
    if assessment is None:
        return None
    outcome = assessment.get("outcome")
    if not isinstance(outcome, str):
        return None
    return _EVALUATIONS.get(outcome.strip().upper())


def _payout(
    lease_status: str,
    binding_state: str,
    settlement_state: str | None,
) -> Literal["paid", "pending", "not_paid"] | None:
    # Paid only when this lease settled and the settlement released. A failed
    # settlement or a cancelled binding is not paid, even if another state is open.
    if (
        lease_status == LeaseStatus.SETTLED.value
        and settlement_state == SettlementState.RELEASED.value
    ):
        return "paid"
    if (
        settlement_state == SettlementState.FAILED.value
        or binding_state == BindingState.CANCELLED.value
    ):
        return "not_paid"
    if settlement_state is not None and settlement_state not in _TERMINAL_SETTLEMENTS:
        return "pending"
    return None


def _uuid_text(value: object) -> str:
    return str(uuid.UUID(str(value)))


def _pr_number(lease_pr: int | None, binding_pr: int | None) -> int | None:
    if lease_pr is not None:
        return int(lease_pr)
    if binding_pr is not None:
        return int(binding_pr)
    return None


def _statement(user_id: uuid.UUID):
    return (
        select(
            GitHubLease.id.label("lease_id"),
            GitHubLease.status.label("status"),
            GitHubLease.work_expires_at.label("work_expires_at"),
            GitHubLease.pr_number.label("lease_pr_number"),
            GitHubBountyBinding.bounty_id.label("bounty_id"),
            GitHubBountyBinding.repo_full_name.label("repo"),
            GitHubBountyBinding.issue_number.label("issue_number"),
            GitHubBountyBinding.pr_number.label("binding_pr_number"),
            GitHubBountyBinding.state.label("binding_state"),
            GitHubBountyBinding.last_assessment.label("last_assessment"),
            AcceptanceSpec.amount_ate.label("amount_ate"),
            GitHubSettlement.state.label("settlement_state"),
        )
        .select_from(GitHubLease)
        .join(GitHubBountyBinding, GitHubBountyBinding.id == GitHubLease.binding_id)
        .join(AcceptanceSpec, AcceptanceSpec.id == GitHubLease.spec_id)
        # A binding has one settlement, created with the lease it settles. Earlier
        # leases on the same binding must not show that settlement's payout.
        .outerjoin(
            GitHubSettlement,
            and_(
                GitHubSettlement.binding_id == GitHubBountyBinding.id,
                GitHubSettlement.solver_agent_id == GitHubLease.agent_id,
                GitHubLease.status == LeaseStatus.SETTLED.value,
            ),
        )
        .where(GitHubLease.user_id == user_id)
        .order_by(GitHubLease.created_at.desc(), GitHubLease.id.desc())
        .limit(_MAX_LEASES)
    )


def _item(row) -> MyLeaseOut:
    settlement_state = row.settlement_state
    return MyLeaseOut(
        lease_id=_uuid_text(row.lease_id),
        bounty_id=_uuid_text(row.bounty_id),
        repo=str(row.repo),
        issue_number=int(row.issue_number),
        status=row.status,
        work_expires_at=_iso_utc(row.work_expires_at),
        pr_number=_pr_number(row.lease_pr_number, row.binding_pr_number),
        evaluation=_evaluation(row.last_assessment),
        payout=_payout(
            str(row.status),
            str(row.binding_state),
            None if settlement_state is None else str(settlement_state),
        ),
        amount_ate=int(row.amount_ate),
    )


async def list_my_leases(db: AsyncSession, user_id: uuid.UUID) -> MyLeasesOut:
    rows = (await db.execute(_statement(user_id))).all()
    return MyLeasesOut(items=[_item(row) for row in rows])
