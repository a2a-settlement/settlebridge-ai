"""Verifier quota: daily slot, spend cap, and hourly per-repo rate limit.

SQLite plus a sync session bridge, same shape as tests/test_spend.py.
No network.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models.fix_verifier import FixVerification, VerifierQuota
from app.models.github import GitHubInstallation, GitHubRepoLink
from app.services.verifier_quota import (
    QuotaExceeded,
    charge_verification_spend,
    release_verification_slot,
    reserve_verification_slot,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
INSTALLATION = 42
REPO = "acme/widget"


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


class Bridge:
    """Async facade over a sync SQLite session."""

    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()

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
        dbapi_connection.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    for model in (GitHubInstallation, GitHubRepoLink, FixVerification, VerifierQuota):
        model.__table__.create(eng)
    return eng


def open_db():
    eng = _engine()
    return eng, Bridge(Session(eng, expire_on_commit=False))


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db():
    eng, bridge = open_db()
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


@pytest.fixture()
def caps(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 2)
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 5)
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_COST_CAP_CENTS", 2500)
    monkeypatch.setattr(settings, "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION", 500)
    monkeypatch.setattr("app.services.verifier_quota._utcnow", lambda: NOW)


def _quota(db: Bridge, installation_id: int = INSTALLATION) -> VerifierQuota | None:
    db.sync.expire_all()
    return db.sync.scalar(
        select(VerifierQuota).where(VerifierQuota.installation_id == installation_id)
    )


def _seed_quota(db: Bridge, *, used: int = 0, spend: int = 0) -> None:
    db.sync.add(
        VerifierQuota(
            id=uuid.uuid4(),
            installation_id=INSTALLATION,
            period="2026-11-11",
            used=used,
            limit=2,
            spend_cents=spend,
            updated_at=NOW,
        )
    )
    db.sync.commit()


def _seed_verification(db: Bridge, *, minutes_ago: int, sha: str) -> None:
    created = NOW - timedelta(minutes=minutes_ago)
    db.sync.add(
        FixVerification(
            id=uuid.uuid4(),
            installation_id=INSTALLATION,
            repo_full_name=REPO,
            vuln_id=f"GHSA-{sha[:4]}",
            pr_number=1,
            base_sha="a" * 40,
            fix_sha=sha,
            state="done",
            inputs={},
            result={},
            cost_cents=0,
            embargo_state="none",
            solver_kind="unknown",
            created_at=created,
        )
    )
    db.sync.commit()


def test_daily_slot_stops_at_the_cap_and_release_returns_it(db, caps):
    first = _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    second = _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert first.period == second.period == "2026-11-11"
    assert _quota(db).used == 2
    with pytest.raises(QuotaExceeded) as raised:
        _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert raised.value.reason == "daily_cap"
    assert raised.value.retry_after == 12 * 3600
    assert _quota(db).used == 2

    _run(release_verification_slot(db, second))
    assert _quota(db).used == 1
    _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert _quota(db).used == 2


def test_spend_cap_is_reserved_up_front_and_actual_cost_is_charged(db, caps):
    _seed_quota(db, spend=2000)
    held = _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert _quota(db).used == 1
    assert _quota(db).spend_cents == 2000
    _run(charge_verification_spend(db, held, 40))
    assert _quota(db).spend_cents == 2040
    assert _quota(db).used == 1

    _seed_spend = _quota(db)
    _seed_spend.spend_cents = 2001
    db.sync.commit()
    with pytest.raises(QuotaExceeded) as raised:
        _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert raised.value.reason == "daily_cap"
    assert raised.value.retry_after == 12 * 3600
    assert _quota(db).used == 1
    assert _quota(db).spend_cents == 2001

    _run(charge_verification_spend(db, held, 0))
    assert _quota(db).spend_cents == 2001


def test_spend_at_the_exact_cap_still_admits_one_slot(db, caps):
    _seed_quota(db, spend=2000)
    held = _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    _run(charge_verification_spend(db, held, 500))
    assert _quota(db).spend_cents == 2500
    with pytest.raises(QuotaExceeded):
        _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))


def test_hourly_rate_limit_reports_when_the_oldest_row_ages_out(db, caps, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 2)
    _seed_verification(db, minutes_ago=50, sha="1" * 40)
    _seed_verification(db, minutes_ago=10, sha="2" * 40)
    with pytest.raises(QuotaExceeded) as raised:
        _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert raised.value.reason == "rate_limit"
    # Oldest row is 50 minutes old, so one slot frees in 10 minutes.
    assert raised.value.retry_after == 600
    assert _quota(db) is None


def test_rate_limit_zero_retries_in_an_hour(db, caps, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 0)
    with pytest.raises(QuotaExceeded) as raised:
        _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert raised.value.reason == "rate_limit"
    assert raised.value.retry_after == 3600


def test_next_utc_day_gets_a_fresh_period(db, caps, monkeypatch):
    _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    later = NOW + timedelta(days=1)
    monkeypatch.setattr("app.services.verifier_quota._utcnow", lambda: later)
    _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    db.sync.expire_all()
    rows = list(db.sync.scalars(select(VerifierQuota).order_by(VerifierQuota.period)))
    assert [(row.period, row.used) for row in rows] == [("2026-11-11", 1), ("2026-11-12", 1)]


def test_rows_older_than_an_hour_do_not_count(db, caps, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 1)
    _seed_verification(db, minutes_ago=61, sha="3" * 40)
    held = _run(reserve_verification_slot(db, installation_id=INSTALLATION, repo_full_name=REPO))
    assert held.period == "2026-11-11"
    assert _quota(db).used == 1
    assert int(db.sync.scalar(select(func.count()).select_from(FixVerification))) == 1
