"""Dedupe, cancellation, and challenge dispatch for the GitHub job queue."""

# ruff: noqa: E402

import asyncio
import logging
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models.github import GitHubJob
from app.services import github_jobs
from app.services.github_errors import RetryableJobError
from app.services.github_jobs import cancel_jobs, claim_due_jobs, enqueue_job, run_job

KEY = "chal:1:fetch"
WHEN = datetime(2026, 11, 7, 18, 30, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _engine():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _record):
        # Keep SAVEPOINT from committing the outer SQLite transaction.
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    GitHubJob.__table__.create(engine)
    with engine.begin() as conn:
        # Postgres partial unique index is emitted as a full unique index on SQLite,
        # which would reject a finished key. The open-row SELECT is the check under test.
        conn.execute(text("DROP INDEX IF EXISTS uq_github_jobs_open_dedupe_key"))
    return engine


class _Nested:
    def __init__(self, sync: Session):
        self._sync = sync
        self._trans = None

    async def __aenter__(self):
        self._trans = self._sync.begin_nested()
        self._trans.__enter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        trans = self._trans
        self._trans = None
        if trans is not None:
            trans.__exit__(exc_type, exc, tb)
        return False


class Bridge:
    """Async session facade over a sync SQLite session."""

    def __init__(self, sync: Session):
        self.sync = sync
        self.commits = 0

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    def begin_nested(self):
        return _Nested(self.sync)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()
        self.commits += 1

    async def rollback(self):
        self.sync.rollback()


@pytest.fixture()
def db():
    engine = _engine()
    sync = Session(engine, expire_on_commit=False)
    bridge = Bridge(sync)
    try:
        yield bridge
    finally:
        sync.close()
        engine.dispose()


def _rows(db: Bridge) -> list[GitHubJob]:
    return list(db.sync.scalars(select(GitHubJob)).all())


def test_enqueue_dedupes_while_open_and_allows_a_finished_key(db: Bridge):
    before = datetime.now(timezone.utc)
    first = _run(enqueue_job(db, kind="challenge_fetch", payload={"n": 1}, dedupe_key=KEY))
    after = datetime.now(timezone.utc)
    assert first is not None
    assert first.kind == "challenge_fetch"
    assert first.payload == {"n": 1}
    assert first.dedupe_key == KEY
    assert first.status == "pending"
    assert before <= first.next_run_at <= after
    assert db.commits == 0

    assert _run(enqueue_job(db, kind="challenge_fetch", payload={"n": 2}, dedupe_key=KEY)) is None
    first.status = "running"
    assert _run(enqueue_job(db, kind="challenge_fetch", payload={"n": 3}, dedupe_key=KEY)) is None

    other = _run(enqueue_job(db, kind="installation", payload={}, dedupe_key="other"))
    assert other is not None
    assert other.dedupe_key == "other"

    bare_a = _run(enqueue_job(db, kind="installation", payload={"x": 1}, dedupe_key=None))
    bare_b = _run(enqueue_job(db, kind="installation", payload={"x": 2}, dedupe_key=None))
    assert bare_a is not None and bare_b is not None
    assert bare_a.id != bare_b.id

    current = first
    for terminal in ("done", "cancelled", "failed", "dead"):
        current.status = terminal
        nxt = _run(
            enqueue_job(
                db,
                kind="challenge_fetch",
                payload={"step": terminal},
                dedupe_key=KEY,
                run_at=WHEN,
            )
        )
        assert nxt is not None
        assert nxt.id != current.id
        assert nxt.status == "pending"
        assert nxt.next_run_at == WHEN
        assert nxt.payload == {"step": terminal}
        current = nxt

    assert db.commits == 0
    assert len(_rows(db)) >= 2
    db.sync.rollback()
    assert _rows(db) == []


class _EmptyScalars:
    def first(self):
        return None


class _EmptyResult:
    def scalars(self):
        return _EmptyScalars()


class _BoomNested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RaceDB:
    def __init__(self):
        self.events: list[str] = []
        self.commits = 0

    async def execute(self, _statement):
        self.events.append("select")
        return _EmptyResult()

    def add(self, _obj):
        self.events.append("add")

    def begin_nested(self):
        self.events.append("savepoint")
        return _BoomNested()

    async def flush(self):
        self.events.append("flush")
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    async def commit(self):
        self.commits += 1


def test_enqueue_integrity_error_returns_none_without_committing():
    db = _RaceDB()
    found = _run(enqueue_job(db, kind="challenge_fetch", payload={"n": 1}, dedupe_key=KEY))
    assert found is None
    assert db.commits == 0
    assert db.events == ["select", "savepoint", "add", "flush"]


def _stored(db: Bridge, **kwargs) -> GitHubJob:
    fields = {
        "id": uuid.uuid4(),
        "kind": "challenge_fetch",
        "payload": {},
        "status": "pending",
        "next_run_at": datetime.now(timezone.utc),
    }
    fields.update(kwargs)
    job = GitHubJob(**fields)
    db.sync.add(job)
    db.sync.flush()
    return job


def test_cancel_prefix_escapes_like_wildcards_and_does_not_commit(db: Bridge):
    locked = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    percent_yes = _stored(db, dedupe_key="a%b:yes")
    percent_run = _stored(db, dedupe_key="a%b:run", status="running", locked_until=locked)
    percent_over = _stored(db, dedupe_key="aXXXb:no")
    percent_done = _stored(db, dedupe_key="a%b:done", status="done")
    underscore_yes = _stored(db, dedupe_key="a_b:yes")
    underscore_over = _stored(db, dedupe_key="axb:no")
    nested = _stored(db, dedupe_key="xa%b:no")
    missing_key = _stored(db, dedupe_key=None)
    failed = _stored(db, dedupe_key="a%b:failed", status="failed", last_error="boom")

    assert _run(cancel_jobs(db, dedupe_key_prefix="a%b")) == 2
    assert percent_yes.status == "cancelled"
    assert percent_yes.last_error == "cancelled"
    assert percent_yes.locked_until is None
    assert percent_run.status == "cancelled"
    assert percent_run.locked_until is None
    assert percent_run.last_error == "cancelled"
    assert percent_over.status == "pending"
    assert percent_done.status == "done"
    assert percent_done.last_error is None
    assert nested.status == "pending"
    assert missing_key.status == "pending"
    assert failed.status == "failed"
    assert failed.last_error == "boom"
    assert underscore_yes.status == "pending"
    assert underscore_over.status == "pending"

    assert _run(cancel_jobs(db, dedupe_key_prefix="a_b")) == 1
    assert underscore_yes.status == "cancelled"
    assert underscore_yes.last_error == "cancelled"
    assert underscore_over.status == "pending"
    assert db.commits == 0
    db.sync.rollback()
    assert _rows(db) == []


class _Scalars:
    def all(self):
        return []


class _Result:
    def scalars(self):
        return _Scalars()


class _ClaimDB:
    def __init__(self):
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        return _Result()

    async def commit(self):
        return None


def test_claim_due_jobs_excludes_cancelled():
    db = _ClaimDB()
    assert _run(claim_due_jobs(db, limit=5)) == []
    sql = " ".join(
        str(
            db.stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).split()
    )
    assert "status = 'pending'" in sql
    assert "status = 'running'" in sql
    assert "cancelled" not in sql


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
    job.kind = overrides.get("kind", "issue_labeled")
    job.status = overrides.get("status", "running")
    job.attempts = overrides.get("attempts", 1)
    job.locked_until = overrides.get("locked_until", datetime.now(timezone.utc))
    job.last_error = overrides.get("last_error", None)
    job.next_run_at = overrides.get("next_run_at", datetime(2020, 1, 1, tzinfo=timezone.utc))
    return job


def _factory_for(job):
    return _Factory({job.id: job})


def test_run_job_skips_a_cancelled_job(monkeypatch, caplog):
    job = _job(status="cancelled", last_error="cancelled")

    async def handler(db, current):
        raise AssertionError("cancelled job should not run")

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    caplog.set_level(logging.INFO, logger="app.services.github_jobs")
    factory = _factory_for(job)
    _run(run_job(factory, job.id))
    assert job.status == "cancelled"
    assert job.last_error == "cancelled"
    assert factory.sessions[0].commits == 0
    assert "cancelled; skipping" in caplog.text


def test_run_job_does_not_mark_done_when_cancelled_while_running(monkeypatch, caplog):
    job = _job(status="running", last_error="previous")
    replacement = _job(
        id=job.id,
        status="cancelled",
        last_error="cancelled",
        attempts=job.attempts,
        locked_until=None,
    )
    factory = _factory_for(job)

    async def handler(db, current):
        assert current is job
        await db.commit()
        db.store[job.id] = replacement

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    caplog.set_level(logging.INFO, logger="app.services.github_jobs")
    _run(run_job(factory, job.id))
    assert job.status == "running"
    assert replacement.status == "cancelled"
    assert replacement.last_error == "cancelled"
    assert replacement.locked_until is None
    assert factory.sessions[0].commits == 1
    assert "cancelled while running" in caplog.text
    assert f"{job.id} done" not in caplog.text


def test_challenge_job_is_dispatched_when_engine_enabled(monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    job = _job(kind="challenge_validate", status="running")
    seen = {}

    async def challenge(db, current):
        seen["job"] = current

    async def bounty(db, current):
        raise AssertionError("bounty handler")

    monkeypatch.setattr("app.services.challenge_orchestrator.handle_challenge_job", challenge)
    monkeypatch.setattr(github_jobs, "handle_job", bounty)
    _run(run_job(_factory_for(job), job.id))
    assert seen["job"] is job
    assert job.status == "done"
    assert job.locked_until is None
    assert job.last_error is None


def test_challenge_job_is_cancelled_when_engine_disabled(monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", False)
    job = _job(
        kind="challenge_report",
        status="running",
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        last_error="previous",
    )
    called = {"n": 0}

    async def challenge(db, current):
        called["n"] += 1

    async def bounty(db, current):
        called["n"] += 1

    monkeypatch.setattr("app.services.challenge_orchestrator.handle_challenge_job", challenge)
    monkeypatch.setattr(github_jobs, "handle_job", bounty)
    factory = _factory_for(job)
    _run(run_job(factory, job.id))
    assert called["n"] == 0
    assert job.status == "cancelled"
    assert job.last_error == "challenge engine disabled"
    assert job.locked_until is None
    assert factory.sessions[0].commits == 1


def test_non_challenge_job_still_uses_the_bounty_handler(monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    job = _job(kind="issue_labeled", status="running")
    seen = {"bounty": 0, "challenge": 0}

    async def challenge(db, current):
        seen["challenge"] += 1

    async def bounty(db, current):
        seen["bounty"] += 1

    monkeypatch.setattr("app.services.challenge_orchestrator.handle_challenge_job", challenge)
    monkeypatch.setattr(github_jobs, "handle_job", bounty)
    _run(run_job(_factory_for(job), job.id))
    assert seen == {"bounty": 1, "challenge": 0}
    assert job.status == "done"


def test_safe_error_redacts_challenge_signing_key(monkeypatch):
    secret = "challenge-signing-key-value"
    monkeypatch.setattr(settings, "CHALLENGE_REPORT_SIGNING_KEY", secret)
    job = _job(attempts=1)

    async def handler(db, current):
        raise RetryableJobError(f"leaked {secret} in the message")

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    _run(run_job(_factory_for(job), job.id))
    assert job.status == "pending"
    assert secret not in job.last_error
    assert "[redacted]" in job.last_error


def test_cancel_jobs_refuses_an_empty_prefix():
    import asyncio

    from app.services.github_jobs import cancel_jobs

    with pytest.raises(ValueError):
        asyncio.run(cancel_jobs(None, dedupe_key_prefix=""))
