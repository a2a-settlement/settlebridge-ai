"""Job backoff, claim, and run_job state transitions. No database."""

# ruff: noqa: E402

import asyncio
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.models.github import GitHubJob
from app.services import github_jobs
from app.services.github_errors import PermanentJobError, RetryableJobError
from app.services.github_jobs import (
    claim_due_jobs,
    compute_backoff,
    enqueue_pending_settlements,
    failure_transition,
    run_due_jobs,
    run_github_job_loop,
    run_job,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    ("attempts", "seconds"),
    [
        (0, 10),
        (1, 20),
        (2, 40),
        (3, 80),
        (8, 2560),
        (9, 3600),
        (12, 3600),
    ],
)
def test_compute_backoff(attempts, seconds):
    assert compute_backoff(attempts) == timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("attempts", "max_attempts", "permanent", "status", "backoff_s"),
    [
        (1, 8, True, "failed", None),
        (8, 8, True, "failed", None),
        (8, 8, False, "dead", None),
        (9, 8, False, "dead", None),
        (7, 8, False, "pending", 1280),
        (0, 8, False, "pending", 10),
        (3, 8, False, "pending", 80),
    ],
)
def test_failure_transition(attempts, max_attempts, permanent, status, backoff_s):
    new_status, delay = failure_transition(attempts, max_attempts, permanent)
    assert new_status == status
    if backoff_s is None:
        assert delay is None
    else:
        assert delay == timedelta(seconds=backoff_s)


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


class _ClaimDB:
    def __init__(self, rows):
        self.rows = rows
        self.stmt = None
        self.committed = False

    async def execute(self, stmt):
        self.stmt = stmt
        return _Result(self.rows)

    async def commit(self):
        self.committed = True


def _row(attempts, status="pending"):
    row = type("Row", (), {})()
    row.id = uuid.uuid4()
    row.status = status
    row.attempts = attempts
    row.locked_until = None
    row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    return row


def test_claim_due_jobs_sql_skips_locked_rows():
    db = _ClaimDB([])
    ids = _run(claim_due_jobs(db, limit=5, lock_seconds=300))
    assert ids == []
    assert db.committed is True
    sql = str(db.stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "next_run_at" in sql
    assert "locked_until" in sql
    assert db.stmt._for_update_arg.skip_locked is True


def test_claim_due_jobs_marks_rows_running():
    first = _row(0)
    second = _row(4, status="running")
    db = _ClaimDB([first, second])
    before = datetime.now(timezone.utc)
    ids = _run(claim_due_jobs(db, limit=10, lock_seconds=120))
    after = datetime.now(timezone.utc)
    assert ids == [first.id, second.id]
    assert db.committed is True
    for row, attempts in ((first, 1), (second, 5)):
        assert row.status == "running"
        assert row.attempts == attempts
        assert row.locked_until >= before + timedelta(seconds=120)
        assert row.locked_until <= after + timedelta(seconds=120)


class _MemSession:
    def __init__(self, store: dict):
        self.store = store
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, pk):
        return self.store.get(pk)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Factory:
    def __init__(self, store: dict):
        self.store = store
        self.sessions: list[_MemSession] = []

    def __call__(self):
        session = _MemSession(self.store)
        self.sessions.append(session)
        return session


def _job(**overrides):
    job = type("Job", (), {})()
    job.id = overrides.get("id", uuid.uuid4())
    job.status = overrides.get("status", "running")
    job.attempts = overrides.get("attempts", 1)
    job.locked_until = overrides.get("locked_until", datetime.now(timezone.utc))
    job.last_error = overrides.get("last_error", "previous")
    job.next_run_at = overrides.get("next_run_at", datetime(2020, 1, 1, tzinfo=timezone.utc))
    return job


def _factory_for(job):
    return _Factory({job.id: job})


def test_run_job_success_marks_done(monkeypatch):
    job = _job(attempts=2, last_error="previous")

    async def handler(db, current):
        assert current is job

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    factory = _factory_for(job)
    _run(run_job(factory, job.id))
    assert job.status == "done"
    assert job.locked_until is None
    assert job.last_error is None
    assert job.attempts == 2
    assert len(factory.sessions) == 1
    assert factory.sessions[0].commits == 1
    assert factory.sessions[0].rollbacks == 0


def test_run_job_retryable_error_stays_pending(monkeypatch):
    job = _job(attempts=2)

    async def handler(db, current):
        raise RetryableJobError("temporary outage")

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    factory = _factory_for(job)
    before = datetime.now(timezone.utc)
    _run(run_job(factory, job.id))
    assert job.status == "pending"
    assert job.attempts == 2
    assert job.locked_until is None
    assert job.next_run_at >= before + timedelta(seconds=40)
    assert job.next_run_at <= datetime.now(timezone.utc) + timedelta(seconds=40)
    assert "temporary outage" in job.last_error
    assert factory.sessions[0].rollbacks == 1
    assert factory.sessions[0].commits == 0
    assert factory.sessions[1].commits == 1


def test_run_job_permanent_error_fails(monkeypatch):
    sentinel = datetime(2020, 1, 1, tzinfo=timezone.utc)
    job = _job(attempts=1, next_run_at=sentinel)

    async def handler(db, current):
        raise PermanentJobError("spec missing")

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    _run(run_job(_factory_for(job), job.id))
    assert job.status == "failed"
    assert job.attempts == 1
    assert job.locked_until is None
    assert job.next_run_at == sentinel
    assert "spec missing" in job.last_error


def test_run_job_generic_error_retries(monkeypatch):
    job = _job(attempts=3)

    async def handler(db, current):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    before = datetime.now(timezone.utc)
    _run(run_job(_factory_for(job), job.id))
    assert job.status == "pending"
    assert job.attempts == 3
    assert job.locked_until is None
    assert job.next_run_at >= before + compute_backoff(3)


def test_run_job_generic_error_at_max_attempts_is_dead(monkeypatch):
    job = _job(attempts=settings.GITHUB_JOB_MAX_ATTEMPTS)

    async def handler(db, current):
        raise RuntimeError("x" * 5000)

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    _run(run_job(_factory_for(job), job.id))
    assert job.status == "dead"
    assert job.attempts == settings.GITHUB_JOB_MAX_ATTEMPTS
    assert job.locked_until is None
    assert job.last_error is not None
    assert len(job.last_error) == 2000
    assert job.last_error.startswith("RuntimeError:")


def test_run_job_redacts_secrets(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "super-secret-value")
    job = _job(attempts=1)

    async def handler(db, current):
        raise RetryableJobError("leaked super-secret-value in the message")

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    _run(run_job(_factory_for(job), job.id))
    assert job.status == "pending"
    assert "super-secret-value" not in job.last_error
    assert "[redacted]" in job.last_error


def test_run_job_handler_commit_then_success_marks_reloaded_row(monkeypatch):
    job = _job(attempts=1, status="running")
    factory = _factory_for(job)
    reloaded = _job(id=job.id, attempts=1, status="running", last_error="keep-until-done")

    async def handler(db, current):
        await db.commit()
        db.store[job.id] = reloaded

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    _run(run_job(factory, job.id))
    assert job.status == "running"
    assert reloaded.status == "done"
    assert reloaded.locked_until is None
    assert reloaded.last_error is None
    assert factory.sessions[0].commits == 2


def test_run_due_jobs_runs_each_claim_in_order(monkeypatch):
    ids = [uuid.uuid4(), uuid.uuid4()]
    seen = {}

    async def fake_claim(db, limit, lock_seconds=300):
        seen["limit"] = limit
        seen["db"] = db
        return list(ids)

    ran = []

    async def fake_run(factory, job_id):
        ran.append(job_id)

    monkeypatch.setattr(github_jobs, "claim_due_jobs", fake_claim)
    monkeypatch.setattr(github_jobs, "run_job", fake_run)

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    count = _run(run_due_jobs(lambda: _CM(), limit=4))
    assert count == 2
    assert ran == ids
    assert seen["limit"] == 4


def test_job_loop_logs_and_continues(monkeypatch):
    calls = {"run": 0, "sleep": 0}

    async def fake_run():
        calls["run"] += 1
        if calls["run"] == 1:
            raise RuntimeError("worker blew up")

    async def fake_sleep(seconds):
        assert seconds == settings.GITHUB_JOB_POLL_INTERVAL_S
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(github_jobs, "run_due_jobs", fake_run)
    monkeypatch.setattr(github_jobs.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        _run(run_github_job_loop())
    assert calls["run"] == 2
    assert calls["sleep"] == 2


class _QueuedRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _EnqueueDB:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.commits = 0
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _QueuedRows(self.results.pop(0))

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


def _settlement(state):
    row = type("Settlement", (), {})()
    row.id = uuid.uuid4()
    row.state = state
    return row


def _advance_job(settlement_id, status="pending", kind="advance_settlement"):
    job = type("Job", (), {})()
    job.kind = kind
    job.status = status
    job.payload = {"settlement_id": str(settlement_id)}
    return job


def test_enqueue_pending_settlements_is_a_noop_while_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", False)
    db = _EnqueueDB([[], []])
    assert _run(enqueue_pending_settlements(db)) == 0
    assert db.statements == []
    assert db.added == []
    assert db.commits == 0


def test_enqueue_pending_settlements_fills_only_open_rows_without_a_live_job(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    pending = _settlement("pending")
    escrow = _settlement("escrow_created")
    delivered = _settlement("delivered")
    released = _settlement("released")
    failed = _settlement("failed")
    jobs = [
        _advance_job(pending.id, status="pending"),
        _advance_job(delivered.id, status="done"),
        _advance_job(escrow.id, status="running", kind="checks_completed"),
        _advance_job(uuid.uuid4(), status="running"),
    ]
    db = _EnqueueDB([[pending, escrow, delivered, released, failed], jobs])
    added = _run(enqueue_pending_settlements(db))
    assert added == 2
    assert db.commits == 1
    assert len(db.statements) == 2
    queued = {row.payload["settlement_id"]: row for row in db.added}
    assert set(queued) == {str(escrow.id), str(delivered.id)}
    for row in db.added:
        assert isinstance(row, GitHubJob)
        assert row.kind == "advance_settlement"
        assert row.status == "pending"
        assert set(row.payload) == {"settlement_id"}

    covered = _EnqueueDB(
        [
            [pending, released],
            [_advance_job(pending.id, status="running")],
        ]
    )
    assert _run(enqueue_pending_settlements(covered)) == 0
    assert covered.added == []
    assert covered.commits == 0


def test_enqueue_pending_settlements_skips_needs_review(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    review = _settlement("needs_review")
    pending = _settlement("pending")
    db = _EnqueueDB([[review], []])
    added = _run(enqueue_pending_settlements(db))
    assert added == 0
    assert db.added == []
    assert db.commits == 0
    mixed = _EnqueueDB([[review, pending], []])
    assert _run(enqueue_pending_settlements(mixed)) == 1
    assert mixed.added[0].payload["settlement_id"] == str(pending.id)
    compiled = str(mixed.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "needs_review" not in compiled


def _loop_session():
    calls = {"enqueue": 0, "expire": 0, "sessions": 0, "sleep": 0}

    class _DB:
        pass

    class _CM:
        async def __aenter__(self):
            calls["sessions"] += 1
            return _DB()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return calls, _DB, _CM


def test_job_loop_enqueues_in_its_own_session_when_settlement_is_enabled(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    calls, _DB, _CM = _loop_session()

    async def fake_run():
        return 0

    async def fake_expire(db):
        calls["expire"] += 1
        assert isinstance(db, _DB)

    async def fake_enqueue(db):
        calls["enqueue"] += 1
        assert isinstance(db, _DB)
        if calls["enqueue"] == 1:
            raise RuntimeError("enqueue down")
        return 0

    async def fake_sleep(seconds):
        assert seconds == settings.GITHUB_JOB_POLL_INTERVAL_S
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(github_jobs, "run_due_jobs", fake_run)
    monkeypatch.setattr(github_jobs, "expire_stale_leases", fake_expire)
    monkeypatch.setattr(github_jobs, "enqueue_pending_settlements", fake_enqueue)
    monkeypatch.setattr(github_jobs, "async_session", lambda: _CM())
    monkeypatch.setattr(github_jobs.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        _run(run_github_job_loop())
    assert calls == {"enqueue": 2, "expire": 2, "sessions": 4, "sleep": 2}


def test_job_loop_still_calls_enqueue_when_settlement_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", False)
    calls, _DB, _CM = _loop_session()

    async def fake_run():
        return 0

    async def fake_expire(db):
        calls["expire"] += 1
        assert isinstance(db, _DB)

    async def fake_enqueue(db):
        calls["enqueue"] += 1
        assert isinstance(db, _DB)
        return 0

    async def fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(github_jobs, "run_due_jobs", fake_run)
    monkeypatch.setattr(github_jobs, "expire_stale_leases", fake_expire)
    monkeypatch.setattr(github_jobs, "enqueue_pending_settlements", fake_enqueue)
    monkeypatch.setattr(github_jobs, "async_session", lambda: _CM())
    monkeypatch.setattr(github_jobs.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        _run(run_github_job_loop())
    assert calls["enqueue"] == 1
    assert calls["expire"] == 1
    assert calls["sessions"] == 2
