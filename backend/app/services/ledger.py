"""Read-only per-bounty ATE ledger for the signed-in user."""

from __future__ import annotations

import csv
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bounty import Bounty
from app.models.github import (
    ACTIVE_LEASE_STATUSES,
    AcceptanceSpec,
    BindingState,
    FunderLedgerEntry,
    GitHubBountyBinding,
    GitHubLease,
    GitHubSettlement,
    LeaseStatus,
    LedgerStatus,
    SettlementState,
)
from app.services import exchange
from app.services.github_bounty_service import escrow_cost

logger = logging.getLogger(__name__)

_ESCROW_PAGE_SIZE = 100
_MAX_ESCROW_PAGES = 10
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_HELD_STATUSES = frozenset({"held", "disputed"})
_SETTLING_STATES = frozenset(
    {
        SettlementState.PENDING.value,
        SettlementState.ESCROW_CREATED.value,
        SettlementState.DELIVERED.value,
        SettlementState.NEEDS_REVIEW.value,
    }
)
_ENDED_LEASES = frozenset({LeaseStatus.EXPIRED.value, LeaseStatus.RELEASED.value})

CSV_COLUMNS = (
    "record",
    "kind",
    "role",
    "bounty_id",
    "repo",
    "issue_number",
    "title",
    "pr_number",
    "state",
    "amount_ate",
    "fee_ate",
    "escrow_id",
    "escrow_status",
    "updated_at",
    "issue_url",
    "pr_url",
    "counterparty_id",
    "counterparty_name",
    "amount",
    "fee_amount",
    "status",
    "held_ate",
    "efficacy_check_at",
    "task_id",
    "created_at",
)


@dataclass(frozen=True)
class FunderRow:
    entry: Any
    binding: Any
    bounty: Any
    spec: Any
    settlement: Any
    leases: tuple[Any, ...]


@dataclass(frozen=True)
class SolverRow:
    lease: Any
    binding: Any
    bounty: Any
    spec: Any
    settlement: Any
    leases: tuple[Any, ...]


@dataclass(frozen=True)
class LedgerRows:
    funder: tuple[FunderRow, ...]
    solver: tuple[SolverRow, ...]


def held_ate(escrow: Any, role: str) -> int:
    if role != "requester":
        return 0
    status = _value(_field(escrow, "status")).lower()
    if status in _HELD_STATUSES:
        return _as_int(_field(escrow, "amount")) + _as_int(_field(escrow, "fee_amount"))
    if status == "partially_released":
        return _as_int(_field(escrow, "holdback_amount")) + _as_int(_field(escrow, "holdback_fee"))
    return 0


def unexplained(held_in_escrow: int, bounty_holds: int, other_holds: int) -> int:
    return held_in_escrow - bounty_holds - other_holds


def free_ate(available: int, committed: int) -> int:
    return available - committed


def committed_ate(entries: Any) -> int:
    total = 0
    for entry in entries:
        if _value(_field(entry, "status")) != LedgerStatus.ADVERTISED.value:
            continue
        total += escrow_cost(_as_int(_field(entry, "amount_ate")))
    return total


def entry_state(role: str, binding: Any, settlement: Any, leases: Any = ()) -> str:
    if role == "solver":
        if _value(_field(settlement, "state")) == SettlementState.RELEASED.value:
            return "paid"
        return "settling"
    if _value(_field(binding, "state")) == BindingState.CANCELLED.value:
        return "cancelled"
    if settlement is not None:
        settlement_state = _value(_field(settlement, "state"))
        if settlement_state == SettlementState.RELEASED.value:
            return "paid"
        if settlement_state in _SETTLING_STATES:
            return "settling"
    lease_rows = leases or ()
    if any(_active_hold(lease) for lease in lease_rows):
        return "reserved"
    if settlement is None and any(_ended_hold(lease) for lease in lease_rows):
        return "refunded"
    return "advertised"


def _escrow_ref(value: Any) -> str | None:
    """A real escrow id. A pending claim marker is not one yet."""
    if not value:
        return None
    text = str(value)
    if text == "pending" or text.startswith("pending:"):
        return None
    return text


def entry_escrow_id(settlement: Any, leases: Any) -> str | None:
    if settlement is not None:
        found = _escrow_ref(_field(settlement, "escrow_id"))
        if found:
            return found
    for lease in leases or ():
        if _active_hold(lease):
            found = _escrow_ref(_field(lease, "hold_escrow_id"))
            if found:
                return found
    return None


def assemble_ledger(
    rows: LedgerRows,
    *,
    account_id: str | None,
    balance: dict[str, int],
    escrows: list,
    exchange_error: str | None,
) -> dict[str, Any]:
    bounty_ids = _bounty_escrow_ids(rows)
    bounty_holds, other_holds, other_escrows = _split_holds(escrows, account_id, bounty_ids)
    committed = committed_ate(row.entry for row in rows.funder)
    available = balance["available"]
    held = balance["held_in_escrow"]
    return {
        "account_id": account_id,
        "balance": {
            "available": available,
            "held_in_escrow": held,
            "total_earned": balance["total_earned"],
            "total_spent": balance["total_spent"],
        },
        "committed_ate": committed,
        "free_ate": free_ate(available, committed),
        "held_breakdown": {
            "bounty_holds": bounty_holds,
            "other_holds": other_holds,
            "unexplained": unexplained(held, bounty_holds, other_holds),
        },
        "entries": build_entries(rows, escrows),
        "other_escrows": other_escrows,
        "exchange_error": exchange_error,
    }


def build_entries(rows: LedgerRows, escrows: list) -> list[dict[str, Any]]:
    matched = _index_escrows(escrows)
    stamped: list[tuple[datetime | None, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows.funder:
        updated, entry = _bounty_entry(
            role="funder",
            binding=row.binding,
            bounty=row.bounty,
            spec=row.spec,
            amount_fallback=row.entry,
            settlement=row.settlement,
            leases=row.leases,
            updated_at=_field(row.entry, "updated_at"),
            matched=matched,
        )
        stamped.append((updated, entry))
        if entry["bounty_id"]:
            seen.add(entry["bounty_id"])
    for row in rows.solver:
        bounty_id = _bounty_id(row.binding, row.bounty)
        if bounty_id and bounty_id in seen:
            continue
        updated, entry = _bounty_entry(
            role="solver",
            binding=row.binding,
            bounty=row.bounty,
            spec=row.spec,
            amount_fallback=None,
            settlement=row.settlement,
            leases=row.leases,
            updated_at=_field(row.lease, "updated_at"),
            matched=matched,
        )
        stamped.append((updated, entry))
    stamped.sort(key=_entry_sort_key, reverse=True)
    return [entry for _, entry in stamped]


def ledger_csv(ledger: Any) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for entry in _field(ledger, "entries") or []:
        writer.writerow(_csv_row("entry", entry))
    for escrow in _field(ledger, "other_escrows") or []:
        writer.writerow(_csv_row("other_escrow", escrow))
    return buffer.getvalue()


def collect_escrows(user: Any) -> list:
    collected: list = []
    seen: set[str] = set()
    for page in range(_MAX_ESCROW_PAGES):
        payload = exchange.list_escrows(
            user, limit=_ESCROW_PAGE_SIZE, offset=page * _ESCROW_PAGE_SIZE
        )
        batch = payload.get("escrows") if isinstance(payload, dict) else None
        if not isinstance(batch, list):
            break
        for escrow in batch:
            key = _escrow_key(escrow)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            collected.append(escrow)
        if len(batch) < _ESCROW_PAGE_SIZE:
            break
    return collected


async def load_ledger_rows(db: AsyncSession, user_id: uuid.UUID) -> LedgerRows:
    funder_raw = (await db.execute(_funder_stmt(user_id))).all()
    solver_raw = (await db.execute(_solver_stmt(user_id))).all()
    binding_ids = [binding.id for _, binding, _, _, _ in funder_raw]
    binding_ids.extend(binding.id for _, binding, _, _, _ in solver_raw)
    leases = await _leases_by_binding(db, binding_ids)
    funder = tuple(
        FunderRow(entry, binding, bounty, spec, settlement, tuple(leases.get(binding.id, ())))
        for entry, binding, bounty, spec, settlement in funder_raw
    )
    solver = tuple(
        SolverRow(lease, binding, bounty, spec, settlement, tuple(leases.get(binding.id, ())))
        for lease, binding, bounty, spec, settlement in solver_raw
    )
    return LedgerRows(funder, solver)


async def get_ledger(db: AsyncSession, user: Any) -> dict[str, Any]:
    rows = await load_ledger_rows(db, user.id)
    account_id: str | None = None
    balance = _zero_balance()
    escrows: list = []
    exchange_error: str | None = None
    try:
        payload = exchange.get_balance(user)
        escrows = collect_escrows(user)
        account_id, balance = _balance_view(payload, user)
    except Exception as exc:
        logger.warning("ledger exchange read failed: %s", type(exc).__name__)
        account_id = None
        balance = _zero_balance()
        escrows = []
        exchange_error = type(exc).__name__
    return assemble_ledger(
        rows,
        account_id=account_id,
        balance=balance,
        escrows=escrows,
        exchange_error=exchange_error,
    )


def _funder_stmt(user_id: uuid.UUID):
    return (
        select(
            FunderLedgerEntry,
            GitHubBountyBinding,
            Bounty,
            AcceptanceSpec,
            GitHubSettlement,
        )
        .join(GitHubBountyBinding, GitHubBountyBinding.id == FunderLedgerEntry.binding_id)
        .join(Bounty, Bounty.id == GitHubBountyBinding.bounty_id)
        .outerjoin(AcceptanceSpec, AcceptanceSpec.id == GitHubBountyBinding.current_spec_id)
        .outerjoin(GitHubSettlement, GitHubSettlement.binding_id == GitHubBountyBinding.id)
        .where(FunderLedgerEntry.funder_user_id == user_id)
    )


def _solver_stmt(user_id: uuid.UUID):
    return (
        select(
            GitHubLease,
            GitHubBountyBinding,
            Bounty,
            AcceptanceSpec,
            GitHubSettlement,
        )
        .join(GitHubBountyBinding, GitHubBountyBinding.id == GitHubLease.binding_id)
        .join(Bounty, Bounty.id == GitHubBountyBinding.bounty_id)
        .outerjoin(AcceptanceSpec, AcceptanceSpec.id == GitHubBountyBinding.current_spec_id)
        .outerjoin(GitHubSettlement, GitHubSettlement.binding_id == GitHubBountyBinding.id)
        .where(
            GitHubLease.user_id == user_id,
            GitHubLease.status == LeaseStatus.SETTLED.value,
        )
    )


async def _leases_by_binding(db: AsyncSession, binding_ids: list) -> dict:
    unique: list = []
    seen: set = set()
    for binding_id in binding_ids:
        if binding_id in seen:
            continue
        seen.add(binding_id)
        unique.append(binding_id)
    if not unique:
        return {}
    stmt = select(GitHubLease).where(GitHubLease.binding_id.in_(unique))
    grouped: dict = {}
    for lease in (await db.execute(stmt)).scalars().all():
        grouped.setdefault(lease.binding_id, []).append(lease)
    return grouped


def _bounty_entry(
    *,
    role: str,
    binding: Any,
    bounty: Any,
    spec: Any,
    amount_fallback: Any,
    settlement: Any,
    leases: Any,
    updated_at: Any,
    matched: dict[str, Any],
) -> tuple[datetime | None, dict[str, Any]]:
    repo = _text(_field(binding, "repo_full_name"))
    issue_number = _as_int(_field(binding, "issue_number"))
    pr_number = _optional_int(_field(binding, "pr_number"))
    escrow_id = entry_escrow_id(settlement, leases)
    fee_ate, escrow_status = _matched_fee(matched, escrow_id)
    updated = _as_utc(updated_at)
    return updated, {
        "kind": "github_bounty",
        "role": role,
        "bounty_id": _bounty_id(binding, bounty),
        "repo": repo,
        "issue_number": issue_number,
        "title": _title(bounty),
        "pr_number": pr_number,
        "state": entry_state(role, binding, settlement, leases),
        "amount_ate": _amount(spec, amount_fallback),
        "fee_ate": fee_ate,
        "escrow_id": escrow_id,
        "escrow_status": escrow_status,
        "updated_at": _iso_or_empty(updated),
        "issue_url": _issue_url(repo, issue_number),
        "pr_url": _pr_url(repo, pr_number),
    }


def _split_holds(
    escrows: list, account_id: str | None, bounty_ids: set[str]
) -> tuple[int, int, list[dict[str, Any]]]:
    bounty_holds = 0
    other_holds = 0
    others: list[dict[str, Any]] = []
    for escrow in escrows:
        role = _escrow_role(escrow, account_id)
        held = held_ate(escrow, role)
        key = _escrow_key(escrow)
        if key is not None and key in bounty_ids:
            bounty_holds += held
            continue
        other_holds += held
        if held > 0:
            others.append(_other_escrow(escrow, role, held))
    return bounty_holds, other_holds, others


def _other_escrow(escrow: Any, role: str, held: int) -> dict[str, Any]:
    if role == "requester":
        counterparty = _field(escrow, "provider_id")
    else:
        counterparty = _field(escrow, "requester_id")
    name = _field(escrow, "counterparty_name")
    return {
        "escrow_id": _escrow_key(escrow) or "",
        "role": role,
        "counterparty_id": "" if counterparty is None else str(counterparty),
        "counterparty_name": None if name is None else str(name),
        "amount": _as_int(_field(escrow, "amount")),
        "fee_amount": _as_int(_field(escrow, "fee_amount")),
        "status": _text(_field(escrow, "status")),
        "held_ate": held,
        "efficacy_check_at": _iso_or_none(_field(escrow, "efficacy_check_at")),
        "task_id": None if _field(escrow, "task_id") is None else str(_field(escrow, "task_id")),
        "created_at": _iso_or_empty(_field(escrow, "created_at")),
    }


def _bounty_escrow_ids(rows: LedgerRows) -> set[str]:
    found: set[str] = set()
    grouped = [(row.settlement, row.leases) for row in rows.funder]
    grouped.extend((row.settlement, row.leases) for row in rows.solver)
    for settlement, leases in grouped:
        if settlement is not None:
            found_id = _escrow_ref(_field(settlement, "escrow_id"))
            if found_id:
                found.add(found_id)
        for lease in leases:
            hold = _escrow_ref(_field(lease, "hold_escrow_id"))
            if hold:
                found.add(hold)
    return found


def _escrow_role(escrow: Any, account_id: str | None) -> str:
    requester = _field(escrow, "requester_id")
    if account_id is not None and requester is not None and str(requester) == account_id:
        return "requester"
    return "provider"


def _matched_fee(matched: dict[str, Any], escrow_id: str | None) -> tuple[int, str | None]:
    if not escrow_id:
        return 0, None
    escrow = matched.get(escrow_id)
    if escrow is None:
        return 0, None
    status = _field(escrow, "status")
    return _as_int(_field(escrow, "fee_amount")), None if status is None else str(status)


def _index_escrows(escrows: list) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for escrow in escrows:
        key = _escrow_key(escrow)
        if key and key not in indexed:
            indexed[key] = escrow
    return indexed


def _escrow_key(escrow: Any) -> str | None:
    value = _field(escrow, "id") or _field(escrow, "escrow_id")
    if not value:
        return None
    return str(value)


def _amount(spec: Any, fallback: Any) -> int:
    if spec is not None and _field(spec, "amount_ate") is not None:
        return _as_int(_field(spec, "amount_ate"))
    if fallback is None or _field(fallback, "amount_ate") is None:
        return 0
    return _as_int(_field(fallback, "amount_ate"))


def _bounty_id(binding: Any, bounty: Any) -> str:
    value = _field(binding, "bounty_id")
    if value is None:
        value = _field(bounty, "id")
    if value is None:
        return ""
    return str(value)


def _title(bounty: Any) -> str:
    title = _field(bounty, "title")
    if title is None:
        return ""
    return str(title)


def _issue_url(repo: str, issue_number: int) -> str:
    return f"https://github.com/{repo}/issues/{issue_number}"


def _pr_url(repo: str, pr_number: int | None) -> str | None:
    if pr_number is None:
        return None
    return f"https://github.com/{repo}/pull/{pr_number}"


def _active_hold(lease: Any) -> bool:
    return _value(_field(lease, "status")) in ACTIVE_LEASE_STATUSES and bool(
        _field(lease, "hold_escrow_id")
    )


def _ended_hold(lease: Any) -> bool:
    return _value(_field(lease, "status")) in _ENDED_LEASES and bool(
        _field(lease, "hold_escrow_id")
    )


def _balance_view(payload: Any, user: Any) -> tuple[str | None, dict[str, int]]:
    if not isinstance(payload, dict):
        raise TypeError
    raw = payload.get("account_id")
    if raw:
        account_id: str | None = str(raw)
    else:
        fallback = getattr(user, "exchange_bot_id", None)
        account_id = str(fallback) if fallback else None
    return account_id, {
        "available": _as_int(payload.get("available")),
        "held_in_escrow": _as_int(payload.get("held_in_escrow")),
        "total_earned": _as_int(payload.get("total_earned")),
        "total_spent": _as_int(payload.get("total_spent")),
    }


def _zero_balance() -> dict[str, int]:
    return {"available": 0, "held_in_escrow": 0, "total_earned": 0, "total_spent": 0}


def _csv_row(record: str, source: Any) -> dict[str, str]:
    row = {column: "" for column in CSV_COLUMNS}
    row["record"] = record
    for column in CSV_COLUMNS:
        if column == "record":
            continue
        value = _field(source, column)
        if value is None:
            continue
        row[column] = str(value)
    return row


def _entry_sort_key(item: tuple[datetime | None, dict]) -> tuple[bool, datetime]:
    updated = item[0]
    if updated is None:
        return (False, _EPOCH)
    return (True, updated)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _value(raw: Any) -> str:
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return str(value)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    stamp = _as_utc(value)
    if stamp is not None:
        return stamp.isoformat()
    return str(value)


def _iso_or_empty(value: Any) -> str:
    rendered = _iso_or_none(value)
    return "" if rendered is None else rendered
