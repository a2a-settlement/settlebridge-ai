"""Lease token, window, cooldown, listing query, and expiry sweep. No database."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import hashlib
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.config import settings
from app.models.github import GitHubBountyBinding, GitHubLease
from app.services import github_bounty_service, github_jobs, github_leases

FIXED = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _sql(stmt) -> str:
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return str(compiled)


class _Capture:
    def __init__(self):
        self.stmt = None
        self.params = None

    async def execute(self, stmt, params=None, **kwargs):
        self.stmt = stmt
        self.params = params
        return self

    def all(self):
        return []

    def scalars(self):
        return self

    def scalar_one(self):
        return 0

    def scalar_one_or_none(self):
        return None


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _SweepDB:
    def __init__(self, leases, bindings=None):
        self.leases = leases
        self.bindings = bindings or {}
        self.stmt = None
        self.committed = False
        self.gets: list = []

    async def execute(self, stmt):
        self.stmt = stmt
        return _Result(self.leases)

    async def get(self, model, pk):
        self.gets.append((model, pk))
        return self.bindings.get(pk)

    async def commit(self):
        self.committed = True


def _lease(**overrides):
    data = {
        "id": uuid.uuid4(),
        "binding_id": uuid.uuid4(),
        "status": "work",
        "work_expires_at": FIXED + timedelta(minutes=5),
        "hard_expires_at": FIXED + timedelta(hours=1),
        "hold_expires_at": None,
        "release_reason": None,
        "pr_number": None,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def _binding(lease, **overrides):
    data = {
        "id": lease.binding_id,
        "state": "needs_review",
        "pr_number": 8,
        "lease_holder_agent_id": "bot-1",
        "evaluated_head_sha": "abc",
        "evaluated_spec_id": uuid.uuid4(),
        "last_assessment": {"outcome": "NEEDS_REVIEW", "findings": [{"severity": "nit"}]},
        "review_cleared_by": "octocat",
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def test_hash_lease_token_is_sha256_hex():
    digest = github_leases.hash_lease_token("sbl_abc")
    assert digest == hashlib.sha256(b"sbl_abc").hexdigest()
    assert len(digest) == 64
    assert digest != github_leases.hash_lease_token("sbl_abd")
    assert "sbl_abc" not in digest


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/widget/pull/12", ("acme/widget", 12)),
        ("https://github.com/A.B/c-d_e/pull/3", ("A.B/c-d_e", 3)),
        ("  https://github.com/acme/widget/pull/9  ", ("acme/widget", 9)),
    ],
)
def test_parse_pr_url_accepts_canonical_https(url, expected):
    assert github_leases.parse_pr_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/widget/pull/12",
        "https://github.com/acme/widget/pull/12/files",
        "https://github.com/acme/widget/pulls/12",
        "https://gitlab.com/acme/widget/pull/12",
        "https://github.com/acme/widget/pull/12/",
        "https://github.com/acme/widget/pull/12#discussion",
        "https://github.com/acme/widget/pull/12?w=1",
        "https://www.github.com/acme/widget/pull/12",
        "https://github.com/acme/widget/issues/12",
        "",
        None,
    ],
)
def test_parse_pr_url_rejects_non_canonical(url):
    assert github_leases.parse_pr_url(url) is None


def test_renewed_window_caps_at_hard_deadline(monkeypatch):
    monkeypatch.setattr(settings, "LEASE_WORK_MINUTES", 15)
    now = FIXED
    later = github_leases.renewed_window(now, now + timedelta(minutes=120))
    assert later == now + timedelta(minutes=15)
    hard = now + timedelta(minutes=10)
    assert github_leases.renewed_window(now, hard) == hard


def test_cooldown_remaining_counts_only_the_recent_window(monkeypatch):
    monkeypatch.setattr(settings, "LEASE_COOLDOWN_EXPIRIES", 3)
    monkeypatch.setattr(settings, "LEASE_COOLDOWN_MINUTES", 60)
    now = FIXED
    inside = [
        now - timedelta(minutes=50),
        now - timedelta(minutes=40),
        now - timedelta(minutes=20),
        now - timedelta(minutes=10),
    ]
    outside = now - timedelta(minutes=90)
    assert github_leases.cooldown_remaining([], now) is None
    assert github_leases.cooldown_remaining(inside[:2], now) is None
    assert github_leases.cooldown_remaining(inside[:2] + [outside], now) is None
    # Oldest of the three most recent is 40 minutes ago, so 20 minutes remain.
    assert github_leases.cooldown_remaining(inside + [outside], now) == 1200
    assert github_leases.cooldown_remaining(inside[:3], now) == 600
    edge = [now - timedelta(minutes=60)] * 3
    assert github_leases.cooldown_remaining(edge, now) == 1


def test_amount_usd_text_uses_formatter(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_ATE_PER_USD", 100)
    original = github_bounty_service.format_usd
    seen = {}

    def fake(amount):
        seen["amount"] = amount
        return "via-formatter"

    monkeypatch.setattr(github_bounty_service, "format_usd", fake)
    assert github_leases.amount_usd_text(1000) == "via-formatter"
    assert seen["amount"] == Decimal("10")

    monkeypatch.setattr(github_bounty_service, "format_usd", original)
    assert github_leases.amount_usd_text(2500) == "25"
    assert github_leases.amount_usd_text(199) == "1.99"


def test_listing_query_excludes_active_leases_and_filters_language():
    db = _Capture()
    rows = _run(github_leases.fetch_advertised_bounties(db, language=None, limit=10))
    assert rows == []
    sql = _sql(db.stmt)
    assert "github_bounty_bindings.state = 'advertised'" in sql
    assert "bounties.status = 'OPEN'" in sql
    assert "NOT (EXISTS" in sql
    assert "github_leases.status IN ('work', 'submitted')" in sql
    assert "unnest" not in sql
    assert "LIMIT 10" in sql

    _run(github_leases.fetch_advertised_bounties(db, language="  ", limit=5))
    assert "unnest" not in _sql(db.stmt)

    _run(github_leases.fetch_advertised_bounties(db, language="Python", limit=5))
    filtered = _sql(db.stmt)
    assert "unnest(bounties.tags)" in filtered
    assert "lower(tag) = 'python'" in filtered


def test_cap_queries_count_active_and_recent_expiries():
    db = _Capture()
    assert _run(github_leases.count_active_leases(db, "bot-1")) == 0
    sql = _sql(db.stmt)
    assert "github_leases.agent_id = 'bot-1'" in sql
    assert "github_leases.status IN ('work', 'submitted')" in sql

    _run(github_leases.fetch_agent_work_leases(db, "bot-1"))
    assert "github_leases.status = 'work'" in _sql(db.stmt)

    _run(github_leases.fetch_expired_timestamps(db, "bot-1", FIXED))
    expired = _sql(db.stmt)
    assert "github_leases.status = 'expired'" in expired
    assert "github_leases.updated_at >= " in expired


def test_expire_sql_locks_skipping_held_rows():
    db = _SweepDB([])
    assert _run(github_leases.expire_stale_leases(db)) == 0
    assert db.committed is True
    sql = _sql(db.stmt)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert db.stmt._for_update_arg.skip_locked is True
    assert "github_leases.status = 'work'" in sql
    assert "work_expires_at" in sql
    assert "hard_expires_at" in sql
    assert "github_leases.status = 'submitted'" in sql
    assert "hold_expires_at" in sql


def test_expire_work_window_and_hard_cap(monkeypatch):
    now = FIXED
    monkeypatch.setattr(github_leases, "_now", lambda: now)
    window = _lease(
        work_expires_at=now - timedelta(seconds=1),
        hard_expires_at=now + timedelta(hours=1),
    )
    hard = _lease(
        work_expires_at=now + timedelta(minutes=5),
        hard_expires_at=now - timedelta(seconds=1),
    )
    live = _lease()
    boundary = _lease(work_expires_at=now, hard_expires_at=now)
    db = _SweepDB([window, hard, live, boundary])
    assert _run(github_leases.expire_stale_leases(db)) == 2
    assert db.committed is True
    assert db.gets == []
    assert window.status == "expired"
    assert window.release_reason == "work_window_elapsed"
    assert hard.status == "expired"
    assert hard.release_reason == "work_window_elapsed"
    assert live.status == "work"
    assert live.release_reason is None
    assert boundary.status == "work"


@pytest.mark.parametrize(
    ("state", "expected_state"),
    [
        ("needs_review", "advertised"),
        ("awaiting_merge", "advertised"),
        ("advertised", "advertised"),
        ("evaluating", "advertised"),
        ("cancelled", "cancelled"),
    ],
)
def test_expire_submitted_hold_unbinds(monkeypatch, state, expected_state):
    now = FIXED
    monkeypatch.setattr(github_leases, "_now", lambda: now)
    lease = _lease(
        status="submitted",
        hold_expires_at=now - timedelta(seconds=1),
        pr_number=8,
    )
    binding = _binding(lease, state=state)
    db = _SweepDB([lease], {binding.id: binding})
    assert _run(github_leases.expire_stale_leases(db)) == 1
    assert db.gets == [(GitHubBountyBinding, lease.binding_id)]
    assert lease.status == "expired"
    assert lease.release_reason == "submitted_hold_elapsed"
    assert binding.state == expected_state
    assert binding.pr_number is None
    assert binding.lease_holder_agent_id is None
    assert binding.evaluated_head_sha is None
    assert binding.evaluated_spec_id is None
    assert binding.last_assessment is None
    assert binding.review_cleared_by is None


@pytest.mark.parametrize("state", ["settlement_pending", "paid"])
def test_expire_skips_submitted_hold_during_settlement(monkeypatch, state):
    now = FIXED
    monkeypatch.setattr(github_leases, "_now", lambda: now)
    lease = _lease(status="submitted", hold_expires_at=now - timedelta(hours=1), pr_number=8)
    binding = _binding(lease, state=state)
    db = _SweepDB([lease], {binding.id: binding})
    assert _run(github_leases.expire_stale_leases(db)) == 0
    assert lease.status == "submitted"
    assert lease.release_reason is None
    assert binding.pr_number == 8
    assert binding.lease_holder_agent_id == "bot-1"
    assert binding.evaluated_head_sha == "abc"
    assert binding.state == state


def test_expire_leaves_submitted_hold_that_has_not_elapsed(monkeypatch):
    now = FIXED
    monkeypatch.setattr(github_leases, "_now", lambda: now)
    lease = _lease(status="submitted", hold_expires_at=now, pr_number=3)
    binding = _binding(lease, state="needs_review")
    db = _SweepDB([lease], {binding.id: binding})
    assert _run(github_leases.expire_stale_leases(db)) == 0
    assert lease.status == "submitted"
    assert binding.pr_number == 8
    assert db.gets == []


def test_job_loop_sweeps_leases_in_their_own_session(monkeypatch):
    calls = {"run": 0, "expire": 0, "sleep": 0, "sessions": 0}

    async def fake_run():
        calls["run"] += 1
        if calls["run"] == 1:
            raise RuntimeError("jobs down")

    class _DB:
        pass

    class _CM:
        async def __aenter__(self):
            calls["sessions"] += 1
            return _DB()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_expire(db):
        calls["expire"] += 1
        assert isinstance(db, _DB)
        if calls["expire"] == 1:
            raise RuntimeError("sweep down")
        return 0

    async def fake_sleep(seconds):
        assert seconds == settings.GITHUB_JOB_POLL_INTERVAL_S
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(github_jobs, "run_due_jobs", fake_run)
    monkeypatch.setattr(github_jobs, "expire_stale_leases", fake_expire)
    monkeypatch.setattr(github_jobs, "async_session", lambda: _CM())
    monkeypatch.setattr(github_jobs.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        _run(github_jobs.run_github_job_loop())
    # Each iteration opens one session for the sweep and one for settlement enqueue.
    assert calls == {"run": 2, "expire": 2, "sleep": 2, "sessions": 4}


def test_renewable_rejects_a_passed_deadline():
    assert github_leases._renewable(_lease(), FIXED) is True
    at_work_edge = _lease(work_expires_at=FIXED, hard_expires_at=FIXED + timedelta(seconds=1))
    assert github_leases._renewable(at_work_edge, FIXED) is True
    stale_work = _lease(work_expires_at=FIXED - timedelta(seconds=1))
    assert github_leases._renewable(stale_work, FIXED) is False
    at_hard_cap = _lease(hard_expires_at=FIXED)
    assert github_leases._renewable(at_hard_cap, FIXED) is False
    past_hard = _lease(hard_expires_at=FIXED - timedelta(seconds=1))
    assert github_leases._renewable(past_hard, FIXED) is False
    assert github_leases._renewable(_lease(status="submitted"), FIXED) is False


def test_agent_claim_lock_is_transaction_scoped():
    db = _Capture()
    _run(github_leases.lock_agent_for_claim(db, "bot-1"))
    sql = str(db.stmt)
    assert "pg_advisory_xact_lock" in sql
    assert "hashtextextended" in sql
    assert db.params == {"agent_id": "bot-1"}


def test_lease_relock_is_for_update_without_skip_locked():
    stmt = github_leases._lease_for_update_stmt(uuid.uuid4())
    sql = _sql(stmt)
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" not in sql
    assert stmt.get_execution_options().get("populate_existing") is True


def test_binding_repo_indexes_are_case_insensitive_and_partial():
    table = GitHubBountyBinding.__table__
    by_name = {index.name: index for index in table.indexes}
    issue = by_name["uq_github_bounty_bindings_lower_repo_issue"]
    pull = by_name["uq_github_bounty_bindings_lower_repo_pr"]
    assert issue.unique is True
    assert pull.unique is True
    dialect = postgresql.dialect()
    issue_sql = str(CreateIndex(issue).compile(dialect=dialect))
    pull_sql = str(CreateIndex(pull).compile(dialect=dialect))
    assert "lower(repo_full_name)" in issue_sql
    assert "issue_number" in issue_sql
    assert "UNIQUE" in issue_sql.upper()
    assert "lower(repo_full_name)" in pull_sql
    assert "pr_number IS NOT NULL" in pull_sql
    unique_pairs = [
        {column.name for column in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert {"repo_full_name", "issue_number"} in unique_pairs
    assert GitHubLease.__table__.c.github_user_id.nullable is False
