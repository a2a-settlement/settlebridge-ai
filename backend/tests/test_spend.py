"""Spend ledger: reservations, per-execution usage, and budget caps.

SQLite plus a sync session bridge, same shape as tests/test_cyber_tasks.py.
The users table is a stub; only the spend tables are created from the models.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.spend import AccountBudget, ExecutionUsage, SpendReservation
from app.services import spend
from app.services.spend import (
    BudgetExceeded,
    BudgetMissing,
    BudgetPaused,
    reserve_spend,
    record_usage,
    reconcile_reservation,
    release_reservation,
    expire_reservations,
    budget_status,
    reservation_exhausted,
)

PERIOD = 1000
PER_JOB = 400
LATER = datetime(2026, 12, 1, tzinfo=timezone.utc)
EARLIER = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


class _Savepoint:
    def __init__(self, sync: Session):
        self._sync = sync
        self._tx = None

    async def __aenter__(self):
        self._tx = self._sync.begin_nested()
        self._tx.__enter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return self._tx.__exit__(exc_type, exc, tb)


class _Bridge:
    """Async facade over a sync SQLite session."""

    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    async def flush(self):
        self.sync.flush()

    def begin_nested(self):
        return _Savepoint(self.sync)

    async def refresh(self, instance, attribute_names=None, with_for_update=None):
        self.sync.refresh(
            instance,
            attribute_names=attribute_names,
            with_for_update=with_for_update,
        )

    def expunge(self, obj):
        self.sync.expunge(obj)


def _engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _sqlite_transactions(dbapi_connection, _record):
        # SAVEPOINT must sit inside a real BEGIN. sqlite3's legacy mode skips
        # BEGIN for SAVEPOINT, so SQLAlchemy owns the transaction boundary.
        dbapi_connection.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE users (id CHAR(32) NOT NULL PRIMARY KEY)"))
    for model in (AccountBudget, SpendReservation, ExecutionUsage):
        model.__table__.create(eng)
    return eng


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db():
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


def _add_budget(
    db: _Bridge,
    *,
    period: int = PERIOD,
    per_job: int = PER_JOB,
    concurrent: int = 2,
    paused: bool = False,
    reserved: int = 0,
    spent: int = 0,
) -> AccountBudget:
    user_id = uuid.uuid4()
    db.sync.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id.hex})
    budget = AccountBudget(
        id=uuid.uuid4(),
        user_id=user_id,
        period_cap_cents=period,
        per_job_cap_cents=per_job,
        max_concurrent_jobs=concurrent,
        period_start=datetime(2026, 11, 1, tzinfo=timezone.utc),
        reserved_cents=reserved,
        spent_cents=spent,
        paused_at=datetime(2026, 11, 2, tzinfo=timezone.utc) if paused else None,
    )
    db.sync.add(budget)
    db.sync.flush()
    return budget


def _fresh(db: _Bridge, model, row_id):
    db.sync.expire_all()
    return db.sync.get(model, row_id)


def _count(db: _Bridge, model) -> int:
    return int(db.sync.scalar(select(func.count()).select_from(model)))


async def _reserve(
    db, budget, *, cents=100, key=None, expires_at=LATER, subject="challenge_attempt:1"
):
    return await reserve_spend(
        db,
        user_id=budget.user_id,
        subject=subject,
        idempotency_key=key or f"spend:{uuid.uuid4()}",
        cents=cents,
        expires_at=expires_at,
    )


async def _use(db, reservation, execution_id, *, cause="initial", cost=10, cpu=1, wall=2, tokens=3):
    return await record_usage(
        db,
        reservation_id=reservation.id,
        execution_id=execution_id,
        cause=cause,
        cpu_seconds=cpu,
        wall_seconds=wall,
        model_tokens=tokens,
        cost_cents=cost,
    )


def test_replay_reserves_once(db):
    async def body():
        budget = _add_budget(db, period=100, per_job=80, concurrent=2)
        first = await _reserve(db, budget, cents=80, key="spend:same")
        again = await _reserve(db, budget, cents=80, key="spend:same")
        held_budget = _add_budget(db, period=100, per_job=80)
        held = await _reserve(db, held_budget, cents=20, key="spend:before-pause")
        held_budget.paused_at = NOW
        db.sync.flush()
        replay = await _reserve(db, held_budget, cents=20, key="spend:before-pause")
        return budget.id, first.id, again.id, held.id, replay.id

    budget_id, first_id, again_id, held_id, replay_id = _run(body())
    assert first_id == again_id
    assert held_id == replay_id
    budget = _fresh(db, AccountBudget, budget_id)
    assert budget.reserved_cents == 80
    assert _count(db, SpendReservation) == 2


def test_replay_key_from_another_budget_is_refused(db):
    async def body():
        mine = _add_budget(db)
        theirs = _add_budget(db)
        await _reserve(db, mine, cents=50, key="spend:shared")
        with pytest.raises(ValueError):
            await _reserve(db, theirs, cents=50, key="spend:shared")
        return theirs.id

    theirs_id = _run(body())
    assert _fresh(db, AccountBudget, theirs_id).reserved_cents == 0


def test_caps_reject_without_reserving(db):
    async def body():
        missing = uuid.uuid4()
        with pytest.raises(BudgetMissing):
            await reserve_spend(
                db,
                user_id=missing,
                subject="challenge_attempt:x",
                idempotency_key="spend:missing",
                cents=10,
                expires_at=LATER,
            )
        with pytest.raises(ValueError):
            await _reserve(db, _add_budget(db), cents=0)
        with pytest.raises(ValueError):
            await _reserve(db, _add_budget(db), cents=-5)

        per_job = _add_budget(db, period=1000, per_job=100)
        with pytest.raises(BudgetExceeded):
            await _reserve(db, per_job, cents=101)
        exact = await _reserve(db, per_job, cents=100)

        period = _add_budget(db, period=100, per_job=100, reserved=40, spent=50)
        with pytest.raises(BudgetExceeded):
            await _reserve(db, period, cents=11)
        fits = await _reserve(db, period, cents=10)

        paused = _add_budget(db, paused=True)
        with pytest.raises(BudgetPaused):
            await _reserve(db, paused, cents=10)

        limited = _add_budget(db, concurrent=1, period=1000, per_job=100)
        await _reserve(db, limited, cents=10, key="spend:open")
        with pytest.raises(BudgetExceeded):
            await _reserve(db, limited, cents=10, key="spend:second")
        return exact.id, fits.id, per_job.id, period.id, limited.id

    exact_id, fits_id, per_job_id, period_id, limited_id = _run(body())
    assert _fresh(db, AccountBudget, per_job_id).reserved_cents == 100
    assert _fresh(db, AccountBudget, period_id).reserved_cents == 50
    assert _fresh(db, AccountBudget, limited_id).reserved_cents == 10
    assert _fresh(db, SpendReservation, exact_id).status == "reserved"
    assert _fresh(db, SpendReservation, fits_id).reserved_cents == 10
    assert _count(db, SpendReservation) == 3


def test_conditional_period_update_allows_only_one(db):
    async def updates():
        budget = _add_budget(db, period=100, per_job=100, concurrent=5, reserved=0, spent=0)
        first = await spend._claim_period(db, budget.id, 60)
        second = await spend._claim_period(db, budget.id, 60)
        return budget.id, first, second

    budget_id, first, second = _run(updates())
    assert first is True
    assert second is False
    assert _fresh(db, AccountBudget, budget_id).reserved_cents == 60

    async def reservations():
        budget = _add_budget(db, period=100, per_job=100, concurrent=5)
        kept = await _reserve(db, budget, cents=60, key="spend:a")
        with pytest.raises(BudgetExceeded):
            await _reserve(db, budget, cents=60, key="spend:b")
        return budget.id, kept.id

    budget_id, kept_id = _run(reservations())
    assert _fresh(db, AccountBudget, budget_id).reserved_cents == 60
    assert _fresh(db, SpendReservation, kept_id).status == "reserved"
    assert _count(db, SpendReservation) == 1


def test_idempotency_conflict_returns_the_hold(db, monkeypatch):
    async def body():
        budget = _add_budget(db, period=200, per_job=100, concurrent=5, reserved=40)
        original = SpendReservation(
            id=uuid.uuid4(),
            budget_id=budget.id,
            subject="challenge_attempt:held",
            idempotency_key="spend:race",
            reserved_cents=40,
            used_cents=0,
            status="reserved",
            expires_at=LATER,
        )
        db.sync.add(original)
        db.sync.flush()
        real = spend._reservation_by_key
        hits = {"n": 0}

        async def hide_until_conflict(session, key):
            hits["n"] += 1
            row = await real(session, key)
            if hits["n"] <= 2:
                return None
            return row

        monkeypatch.setattr(spend, "_reservation_by_key", hide_until_conflict)
        found = await _reserve(db, budget, cents=40, key="spend:race")
        return budget.id, original.id, found.id, hits["n"]

    budget_id, original_id, found_id, hits = _run(body())
    assert hits >= 3
    assert found_id == original_id
    assert _fresh(db, AccountBudget, budget_id).reserved_cents == 40
    assert _count(db, SpendReservation) == 1


def test_usage_is_deduped_by_execution_id(db):
    async def body():
        budget = _add_budget(db)
        reservation = await _reserve(db, budget, cents=100)
        first = await _use(db, reservation, "exec:1", cost=30, cpu=4, wall=5, tokens=6)
        second = await _use(db, reservation, "exec:1", cause="rerun", cost=99)
        other = await _use(db, reservation, "exec:2", cost=20)
        return reservation.id, first.id, second.id, other.id

    reservation_id, first_id, second_id, other_id = _run(body())
    assert first_id == second_id
    assert other_id != first_id
    reservation = _fresh(db, SpendReservation, reservation_id)
    assert reservation.used_cents == 50
    usage = _fresh(db, ExecutionUsage, first_id)
    assert usage.cost_cents == 30
    assert usage.cause == "initial"
    assert usage.charged is True
    assert usage.cpu_seconds == 4
    assert usage.wall_seconds == 5
    assert usage.model_tokens == 6
    assert _count(db, ExecutionUsage) == 2


def test_usage_conflict_does_not_charge_twice(db, monkeypatch):
    async def body():
        budget = _add_budget(db)
        reservation = await _reserve(db, budget, cents=100)
        reservation.used_cents = 15
        existing = ExecutionUsage(
            id=uuid.uuid4(),
            reservation_id=reservation.id,
            execution_id="exec:race",
            cause="initial",
            charged=True,
            cpu_seconds=1,
            wall_seconds=1,
            model_tokens=1,
            cost_cents=15,
        )
        db.sync.add(existing)
        db.sync.flush()
        real = spend._usage_by_execution
        hits = {"n": 0}

        async def hide_until_conflict(session, execution_id):
            hits["n"] += 1
            row = await real(session, execution_id)
            if hits["n"] <= 2:
                return None
            return row

        monkeypatch.setattr(spend, "_usage_by_execution", hide_until_conflict)
        found = await _use(db, reservation, "exec:race", cost=15)
        return reservation.id, existing.id, found.id

    reservation_id, existing_id, found_id = _run(body())
    assert found_id == existing_id
    assert _fresh(db, SpendReservation, reservation_id).used_cents == 15
    assert _count(db, ExecutionUsage) == 1


def test_platform_retry_is_recorded_and_not_charged(db):
    async def body():
        budget = _add_budget(db, period=500, per_job=200)
        reservation = await _reserve(db, budget, cents=100)
        charged = await _use(db, reservation, "exec:real", cost=40)
        retry = await _use(db, reservation, "exec:retry", cause="platform_retry", cost=25)
        closed = await reconcile_reservation(db, reservation.id)
        status = await budget_status(db, budget.user_id)
        return charged.charged, retry.id, closed.used_cents, status

    charged, retry_id, used_cents, status = _run(body())
    retry = _fresh(db, ExecutionUsage, retry_id)
    assert charged is True
    assert retry.charged is False
    assert retry.cause == "platform_retry"
    assert retry.cost_cents == 25
    assert used_cents == 40
    assert status["spent_cents"] == 40
    assert status["reserved_cents"] == 0


def test_reconcile_and_release_account_and_are_idempotent(db):
    async def body():
        budget = _add_budget(db, period=1000, per_job=500)
        reconciled = await _reserve(db, budget, cents=100, key="spend:recon")
        await _use(db, reconciled, "exec:recon", cost=40)
        first = await reconcile_reservation(db, reconciled.id)
        second = await reconcile_reservation(db, reconciled.id)
        late_release = await release_reservation(db, reconciled.id)
        assert late_release.status == "reconciled"

        released = await _reserve(db, budget, cents=80, key="spend:rel")
        await _use(db, released, "exec:rel", cost=25)
        rel = await release_reservation(db, released.id)
        rel_again = await release_reservation(db, released.id)
        late_reconcile = await reconcile_reservation(db, released.id)
        assert late_reconcile.status == "released"

        over = await _reserve(db, budget, cents=50, key="spend:over")
        await _use(db, over, "exec:over", cost=80)
        over_row = await reconcile_reservation(db, over.id)
        status = await budget_status(db, budget.user_id)
        return (
            first.status,
            second.status,
            first.used_cents,
            rel.status,
            rel_again.used_cents,
            over_row.used_cents,
            over_row.reserved_cents,
            status,
        )

    status_a, status_b, used, rel_status, rel_used, over_used, over_held, status = _run(body())
    assert status_a == "reconciled"
    assert status_b == "reconciled"
    assert used == 40
    assert rel_status == "released"
    assert rel_used == 25
    assert over_used == 50
    assert over_held == 50
    assert status == {
        "period_cap_cents": 1000,
        "reserved_cents": 0,
        "spent_cents": 115,
        "available_cents": 885,
        "paused": False,
    }


def test_usage_above_the_hold_is_capped_and_marks_the_reservation_exhausted(db):
    async def body():
        budget = _add_budget(db, period=1000, per_job=100, concurrent=1)
        reservation = await _reserve(db, budget, cents=50, key="spend:cap")
        first = await _use(db, reservation, "exec:cap:1", cost=30)
        partial = (reservation.used_cents, reservation_exhausted(reservation))
        second = await _use(db, reservation, "exec:cap:2", cost=45)
        after_second = (reservation.used_cents, reservation_exhausted(reservation))
        third = await _use(db, reservation, "exec:cap:3", cost=10)
        closed = await reconcile_reservation(db, reservation.id)
        status = await budget_status(db, budget.user_id)
        return first, partial, second, after_second, third, closed.used_cents, status

    first, partial, second, after_second, third, used, status = _run(body())
    assert partial == (30, False)
    assert after_second == (50, True)
    assert (first.cost_cents, second.cost_cents, third.cost_cents) == (30, 45, 10)
    assert used == 50
    assert status["spent_cents"] == 50
    assert status["reserved_cents"] == 0


def test_release_without_usage_returns_the_hold(db):
    async def body():
        budget = _add_budget(db, period=200, per_job=200, concurrent=1)
        reservation = await _reserve(db, budget, cents=80)
        await release_reservation(db, reservation.id)
        follow = await _reserve(db, budget, cents=80, key="spend:after-release")
        status = await budget_status(db, budget.user_id)
        return follow.id, status

    follow_id, status = _run(body())
    assert _fresh(db, SpendReservation, follow_id).status == "reserved"
    assert status["reserved_cents"] == 80
    assert status["spent_cents"] == 0
    assert status["available_cents"] == 120


def test_expire_reservations_uses_release_accounting(db):
    async def body():
        budget = _add_budget(db, period=500, per_job=200, concurrent=4)
        due = await _reserve(db, budget, cents=70, expires_at=EARLIER, key="spend:due")
        await _use(db, due, "exec:due", cost=30)
        waiting = await _reserve(db, budget, cents=40, expires_at=LATER, key="spend:wait")
        exact = await _reserve(db, budget, cents=15, expires_at=NOW, key="spend:exact")
        count = await expire_reservations(db, NOW)
        again = await expire_reservations(db, NOW)
        status = await budget_status(db, budget.user_id)
        return due.id, waiting.id, exact.id, count, again, status

    due_id, waiting_id, exact_id, count, again, status = _run(body())
    assert count == 1
    assert again == 0
    assert _fresh(db, SpendReservation, due_id).status == "expired"
    assert _fresh(db, SpendReservation, waiting_id).status == "reserved"
    assert _fresh(db, SpendReservation, exact_id).status == "reserved"
    assert status["reserved_cents"] == 55
    assert status["spent_cents"] == 30
    assert status["available_cents"] == 415


def test_illegal_status_transitions_raise():
    illegal = (
        ("reconciled", "released"),
        ("reconciled", "expired"),
        ("reconciled", "reserved"),
        ("released", "reconciled"),
        ("released", "expired"),
        ("released", "reserved"),
        ("expired", "reconciled"),
        ("expired", "released"),
        ("expired", "reserved"),
        ("reserved", "reserved"),
    )
    for current, new in illegal:
        with pytest.raises(ValueError):
            spend._require_transition(current, new)
    for new in ("reconciled", "released", "expired"):
        spend._require_transition("reserved", new)


def test_closed_reservations_do_not_move_again(db):
    async def body():
        budget = _add_budget(db, concurrent=4, period=1000, per_job=200)
        expired = await _reserve(db, budget, cents=10, expires_at=EARLIER, key="spend:old")
        await expire_reservations(db, NOW)
        after_reconcile = await reconcile_reservation(db, expired.id)
        after_release = await release_reservation(db, expired.id)
        assert (after_reconcile.status, after_release.status) == ("expired", "expired")
        status = await budget_status(db, budget.user_id)
        assert (status["reserved_cents"], status["spent_cents"]) == (0, 0)
        with pytest.raises(ValueError):
            await _use(db, expired, "exec:after-expire")
        missing = uuid.uuid4()
        with pytest.raises(ValueError):
            await reconcile_reservation(db, missing)
        with pytest.raises(ValueError):
            await record_usage(
                db,
                reservation_id=missing,
                execution_id="exec:missing",
                cause="initial",
                cpu_seconds=0,
                wall_seconds=0,
                model_tokens=0,
                cost_cents=1,
            )
        with pytest.raises(ValueError):
            await _use(db, expired, "exec:bad-cause", cause="not-a-cause")

    _run(body())


def test_reserved_cents_cannot_go_negative(db):
    async def body():
        budget = _add_budget(db, period=500, per_job=200)
        reservation = await _reserve(db, budget, cents=100)
        await _use(db, reservation, "exec:held", cost=20)
        stored = _fresh(db, AccountBudget, budget.id)
        stored.reserved_cents = 0
        db.sync.flush()
        with pytest.raises(ValueError, match="underflow"):
            await reconcile_reservation(db, reservation.id)
        return budget.id, reservation.id

    budget_id, reservation_id = _run(body())
    assert _fresh(db, AccountBudget, budget_id).reserved_cents == 0
    assert _fresh(db, SpendReservation, reservation_id).status == "reserved"
    assert _fresh(db, SpendReservation, reservation_id).used_cents == 20


def test_budget_status_never_reports_negative_available(db):
    async def body():
        budget = _add_budget(db, period=100, per_job=100, spent=140, reserved=0)
        status = await budget_status(db, budget.user_id)
        with pytest.raises(BudgetMissing):
            await budget_status(db, uuid.uuid4())
        paused = _add_budget(db, paused=True, period=50, reserved=10, spent=5)
        paused_status = await budget_status(db, paused.user_id)
        return status, paused_status

    status, paused_status = _run(body())
    assert status["available_cents"] == 0
    assert status["spent_cents"] == 140
    assert paused_status["paused"] is True
    assert paused_status["available_cents"] == 35


def test_unknown_cause_records_nothing(db):
    async def body():
        budget = _add_budget(db)
        reservation = await _reserve(db, budget, cents=40)
        with pytest.raises(ValueError):
            await _use(db, reservation, "exec:bad", cause="sideways", cost=5)
        return reservation.id, budget.id

    reservation_id, budget_id = _run(body())
    assert _fresh(db, SpendReservation, reservation_id).used_cents == 0
    assert _fresh(db, AccountBudget, budget_id).reserved_cents == 40
    assert _count(db, ExecutionUsage) == 0
