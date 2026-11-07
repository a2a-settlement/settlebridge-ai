"""Challenge orchestration: transitions, roles, spend, tamper, and the verifier pass.

SQLite only. Runner, fetch, and validity are mocked. No network.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models.challenge import (
    CLAIM_TRANSITIONS,
    TASK_TRANSITIONS,
    ChallengeArtifact,
    ChallengeAttempt,
    ChallengeClaim,
    ChallengeContract,
    ChallengeParticipant,
    ChallengeTask,
    ChallengeVerdict,
    Finding,
)
from app.models.github import GitHubJob
from app.models.spend import AccountBudget, ExecutionUsage, SpendReservation
from app.schemas.challenge import (
    ChallengeTaskCreate,
    ClaimIn,
    ControlOutcome,
    DifferentialOutcome,
    OracleCitation,
    RunnerResult,
    Scenario,
    ValidationEvidence,
    ValidationOutcome,
)
from app.services.challenge_orchestrator import (
    IdempotencyConflict,
    InvalidTransition,
    RoleConflict,
    _record_adjudication,
    _record_participant,
    _transition_claim,
    cancel_task,
    create_task,
    freeze_contract,
    handle_challenge_job,
    submit_claim,
    transition,
)
from app.services.challenge_runner_client import FetchResult
from app.services.github_bounty_service import spec_content_hash

SECRET = "ghs_SUPERSECRET"
WHEN = datetime(2026, 12, 1, tzinfo=timezone.utc)
BASE = "a" * 40
HEAD = "b" * 40
IMAGE = "sha256:" + ("11" * 32)
LOG = "sha256:" + ("22" * 32)
REF = "sha256:" + ("33" * 32)
PROPERTY = "A private bounty is visible only to a reader of the repository."


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

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()

    @property
    def no_autoflush(self):
        return self.sync.no_autoflush


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

    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE users (id CHAR(32) NOT NULL PRIMARY KEY)"))
    for model in (
        AccountBudget,
        SpendReservation,
        ExecutionUsage,
        ChallengeTask,
        ChallengeContract,
        ChallengeParticipant,
        ChallengeAttempt,
        ChallengeClaim,
        ChallengeArtifact,
        ChallengeVerdict,
        Finding,
        GitHubJob,
    ):
        model.__table__.create(eng)
    with eng.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_github_jobs_open_dedupe_key"))
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


def _body(**overrides) -> ChallengeTaskCreate:
    raw = {
        "repo_full_name": "acme/app",
        "pr_number": 32,
        "base_sha": BASE,
        "target_sha": HEAD,
        "property_text": PROPERTY,
        "failure_classes": ["integrity"],
        "budget_cents": 1000,
        "deadline_at": WHEN,
    }
    raw.update(overrides)
    return ChallengeTaskCreate(**raw)


async def _make_task(db, **overrides) -> ChallengeTask:
    owner = overrides.pop("owner_user_id", uuid.uuid4())
    installation_id = overrides.pop("installation_id", 42)
    idempotency_key = overrides.pop("idempotency_key", f"key-{uuid.uuid4()}")
    return await create_task(
        db,
        owner_user_id=owner,
        installation_id=installation_id,
        body=_body(**overrides),
        idempotency_key=idempotency_key,
    )


def _force_state(db, task, state):
    db.sync.execute(update(ChallengeTask).where(ChallengeTask.id == task.id).values(state=state))
    db.sync.expire_all()
    return db.sync.get(ChallengeTask, task.id)


def _budget(db, user_id, *, period=5000, per_job=5000, paused=False):
    db.sync.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id.hex})
    row = AccountBudget(
        id=uuid.uuid4(),
        user_id=user_id,
        period_cap_cents=period,
        per_job_cap_cents=per_job,
        max_concurrent_jobs=4,
        period_start=datetime(2026, 11, 1, tzinfo=timezone.utc),
        reserved_cents=0,
        spent_cents=0,
        paused_at=WHEN if paused else None,
    )
    db.sync.add(row)
    db.sync.flush()
    return row


def _claim_in(**overrides) -> ClaimIn:
    raw = {
        "failure_class": "integrity",
        "scenario": Scenario(
            precondition="signed out",
            setup="seed two tenants",
            action="open the bounty",
            test_path="challenge_tests/test_gap.py",
        ),
        "oracle_citation": OracleCitation(
            source="property_clause", reference="property", quote="visible only to a reader"
        ),
        "expected": "hidden",
        "observed": "listed",
        "claimed_impact": "another tenant can read the bounty",
    }
    raw.update(overrides)
    return ClaimIn(**raw)


def _tar(path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _result(job, *, status="ok", cpu=1) -> RunnerResult:
    wheel = None
    if job.stage == "dependencies":
        wheel = "sha256:" + hashlib.sha256(job.execution_id.encode()).hexdigest()
    return RunnerResult(
        execution_id=job.execution_id,
        stage=job.stage,
        status=status,
        exit_code=0 if status == "ok" else 1,
        cpu_seconds=cpu,
        wall_seconds=cpu,
        image_digest=IMAGE,
        source_digest=job.source_tarball,
        wheelhouse_manifest=wheel,
        log_digest=LOG,
    )


def _purpose(execution_id: str) -> str:
    return execution_id.split(":")[2]


def _jobs(db) -> list[GitHubJob]:
    return list(db.sync.scalars(select(GitHubJob)).all())


def _enable(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "CHALLENGE_ARTIFACT_ROOT", str(tmp_path))


def _patch_io(monkeypatch, *, runner, fetch=None, precheck=None, decide=None):
    async def token(installation_id, repo_full_name):
        assert repo_full_name == "acme/app"
        return SECRET

    monkeypatch.setattr("app.services.github_scoped_token.scoped_read_token", token)
    monkeypatch.setattr("app.services.challenge_runner_client.run_runner", runner)
    if fetch is not None:
        monkeypatch.setattr("app.services.challenge_runner_client.fetch_source", fetch)

    def _precheck(claim, content):
        return True, "cited"

    monkeypatch.setattr(
        "app.services.challenge_validity.precheck_citation",
        precheck or _precheck,
    )
    if decide is not None:
        monkeypatch.setattr("app.services.challenge_validity.decide", decide)


def _fetch(tmp_path):
    def fetch(*, repo_full_name, sha, token, workdir, include_history=False):
        assert token == SECRET
        assert include_history is False
        path = tmp_path / f"{sha}.tar"
        data = sha.encode()
        path.write_bytes(data)
        return FetchResult(
            source_tarball=str(path),
            source_digest="sha256:" + hashlib.sha256(data).hexdigest(),
        )

    return fetch


def _confirmed(**_kwargs):
    return ValidationOutcome(
        verdict="confirmed",
        reason="observer fired",
        evidence=ValidationEvidence(
            citation_valid=True,
            tamper_clean=True,
            observer_fired=True,
            controls=ControlOutcome(positive_ok=True, negative_ok=True),
            differential=DifferentialOutcome(
                base_fired=False, target_fired=True, classification="new_path"
            ),
            rederivation_fired=True,
            reruns_reproduced=3,
            minimized_reproduced=True,
        ),
    )


_LEGAL_TASK = [(src, dst) for src, dests in TASK_TRANSITIONS.items() for dst in sorted(dests)]
_ILLEGAL_TASK = [
    (src, dst)
    for src in TASK_TRANSITIONS
    for dst in TASK_TRANSITIONS
    if dst not in TASK_TRANSITIONS[src]
]
_LEGAL_CLAIM = [(src, dst) for src, dests in CLAIM_TRANSITIONS.items() for dst in sorted(dests)]
_ILLEGAL_CLAIM = [
    (src, dst)
    for src in CLAIM_TRANSITIONS
    for dst in CLAIM_TRANSITIONS
    if dst not in CLAIM_TRANSITIONS[src]
]


@pytest.mark.parametrize("src,dst", _LEGAL_TASK)
def test_legal_task_transition(db, src, dst):
    task = _run(_make_task(db))
    task = _force_state(db, task, src)

    moved = _run(transition(db, task, dst, "because"))

    assert moved.state == dst
    assert moved.state_reason == "because"


@pytest.mark.parametrize("src,dst", _ILLEGAL_TASK)
def test_illegal_task_transition(db, src, dst):
    task = _run(_make_task(db))
    task = _force_state(db, task, src)

    with pytest.raises(InvalidTransition):
        _run(transition(db, task, dst, "nope"))

    db.sync.expire_all()
    fresh = db.sync.get(ChallengeTask, task.id)
    assert fresh.state == src
    assert fresh.state_reason is None


def _claim_row(state: str) -> ChallengeClaim:
    return ChallengeClaim(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        contract_id=uuid.uuid4(),
        failure_class="integrity",
        scenario={
            "precondition": "p",
            "setup": "s",
            "action": "a",
            "test_path": "challenge_tests/t.py",
        },
        oracle_citation={"source": "property_clause", "reference": "r", "quote": "q"},
        expected="e",
        observed="o",
        claimed_impact="impact",
        state=state,
        claim_hash="cd" * 32,
    )


@pytest.mark.parametrize("src,dst", _LEGAL_CLAIM)
def test_legal_claim_transition(db, src, dst):
    claim = _claim_row(src)
    db.sync.add(claim)
    db.sync.flush()

    moved = _run(_transition_claim(db, claim, dst))

    assert moved.state == dst


@pytest.mark.parametrize("src,dst", _ILLEGAL_CLAIM)
def test_illegal_claim_transition(db, src, dst):
    claim = _claim_row(src)
    db.sync.add(claim)
    db.sync.flush()

    with pytest.raises(InvalidTransition):
        _run(_transition_claim(db, claim, dst))

    db.sync.expire_all()
    assert db.sync.get(ChallengeClaim, claim.id).state == src


def test_concurrent_transition_moves_once(db):
    task = _run(_make_task(db))
    _run(transition(db, task, "frozen", "first"))
    task.state = "draft"

    with pytest.raises(InvalidTransition):
        _run(transition(db, task, "cancelled", "second"))

    db.sync.expire_all()
    assert db.sync.get(ChallengeTask, task.id).state == "frozen"


def test_create_task_is_idempotent_and_rejects_a_different_body(db):
    owner = uuid.uuid4()
    key = "same-key"
    first = _run(_make_task(db, owner_user_id=owner, idempotency_key=key))
    again = _run(_make_task(db, owner_user_id=owner, idempotency_key=key))
    assert again.id == first.id
    assert first.mode == "open_pr"
    assert first.state == "draft"
    retrospective = _run(_make_task(db, pr_number=None, idempotency_key="other-key"))
    assert retrospective.mode == "retrospective"
    assert retrospective.id != first.id

    with pytest.raises(IdempotencyConflict):
        _run(
            _make_task(
                db,
                owner_user_id=owner,
                idempotency_key=key,
                property_text=PROPERTY + " Changed.",
            )
        )
    assert db.sync.scalar(select(ChallengeTask).where(ChallengeTask.idempotency_key == key))


def test_freeze_hashes_the_contract_and_stays_quiet_while_the_engine_is_off(
    db, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", False)
    task = _run(_make_task(db))
    fixture = tmp_path / "fixtures.bin"
    fixture.write_bytes(b"canary-bytes")
    task.environment = {
        "python": "3.11",
        "oracle_sources": ["property_clause", "spec"],
        "oracle_texts": {"spec": {"4.2": "Listings are scoped to the tenant."}},
        "observers": {"integrity": ["state_snapshot"]},
        "fixtures_path": str(fixture),
        "author_operator_id": "author-1",
        "solver_operator_id": "solver-1",
    }
    db.sync.flush()

    frozen = _run(freeze_contract(db, task.id))

    assert frozen.state == "frozen"
    contract = db.sync.get(ChallengeContract, frozen.current_contract_id)
    assert contract.version == 1
    assert contract.content_hash == spec_content_hash(contract.content)
    assert contract.content["property"] == PROPERTY
    assert contract.content["fixtures_digest"].startswith("sha256:")
    assert contract.content["observers"] == {"integrity": ["state_snapshot"]}
    assert contract.content["oracle_texts"] == {
        "spec": {"4.2": "Listings are scoped to the tenant."}
    }
    assert "oracle_texts" not in contract.content["environment"]
    assert "author_operator_id" not in contract.content["environment"]
    assert _jobs(db) == []
    with pytest.raises(InvalidTransition):
        _run(freeze_contract(db, task.id))
    assert len(list(db.sync.scalars(select(ChallengeContract)).all())) == 1


def test_engine_off_handler_does_not_move_the_task(db, monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", False)
    task = _run(_make_task(db))
    task = _run(freeze_contract(db, task.id))
    job = GitHubJob(
        id=uuid.uuid4(),
        kind="challenge_fetch",
        payload={"task_id": str(task.id), "contract_id": str(task.current_contract_id)},
        status="running",
        dedupe_key=f"chal:{task.id}:fetch:1",
    )

    _run(handle_challenge_job(db, job))

    db.sync.expire_all()
    assert db.sync.get(ChallengeTask, task.id).state == "frozen"


def test_role_conflicts_and_challenger_visibility(db):
    task = _run(_make_task(db))
    contract_seen = ["contract:terms", "diff:pair"]

    async def record(role, operator, seen=None):
        return await _record_participant(
            db,
            task,
            role=role,
            operator_id=operator,
            artifacts_seen=seen or contract_seen,
        )

    author = _run(record("author", "person-a", ["contract:terms"]))
    solver = _run(record("solver", "person-a", ["contract:terms"]))
    assert author.operator_id == solver.operator_id
    with pytest.raises(RoleConflict):
        _run(record("challenger", "person-a"))
    challenger = _run(record("challenger", "platform:challenger"))
    assert challenger.platform_operated is True
    verifier = _run(record("verifier", "platform:verifier"))
    assert {challenger.operator_id, verifier.operator_id, "person-a"}
    with pytest.raises(RoleConflict):
        _run(record("adjudicator", "platform:verifier"))
    with pytest.raises(RoleConflict):
        _run(record("verifier", "person-a"))

    source = ChallengeArtifact(
        id=uuid.uuid4(),
        task_id=task.id,
        tenant_user_id=task.owner_user_id,
        kind="source_tarball",
        content_hash="ab" * 32,
        size_bytes=1,
        storage_path="/tmp/source.tar",
        meta={"role": "target"},
    )
    wheel = ChallengeArtifact(
        id=uuid.uuid4(),
        task_id=task.id,
        tenant_user_id=task.owner_user_id,
        kind="wheelhouse_manifest",
        content_hash="cd" * 32,
        size_bytes=1,
        storage_path="digest:sha256:" + ("cd" * 32),
        meta={"role": "target"},
    )
    db.sync.add(source)
    db.sync.add(wheel)
    db.sync.flush()
    seen = _run(
        record(
            "challenger",
            "platform:challenger",
            ["contract:terms", "diff:pair", str(source.id)],
        )
    )
    assert str(source.id) in seen.artifacts_seen
    assert all(
        item.startswith("contract:") or item.startswith("diff:") or item == str(source.id)
        for item in seen.artifacts_seen
    )
    with pytest.raises(RoleConflict):
        _run(
            record(
                "challenger",
                "platform:challenger",
                ["contract:terms", str(wheel.id)],
            )
        )


def test_pipeline_dedupe_keys_verifier_and_no_token(db, monkeypatch, tmp_path, caplog):
    _enable(monkeypatch, tmp_path)
    calls = []

    def runner(job, artifact_root=None):
        calls.append(job)
        blob = json.dumps(job.model_dump(mode="json"))
        assert SECRET not in blob
        return _result(job)

    captured = {}

    def decide(**kwargs):
        captured.update(kwargs)
        return _confirmed()

    _patch_io(monkeypatch, runner=runner, fetch=_fetch(tmp_path), decide=decide)
    task = _run(_make_task(db, budget_cents=1000))
    _budget(db, task.owner_user_id)
    task.environment = {
        "python": "3.11",
        "observers": ["authorization_canary"],
        "oracle_sources": ["property_clause"],
        "reference_digest": REF,
        "test_command": ["pytest", "-q"],
    }
    db.sync.flush()
    task = _run(freeze_contract(db, task.id))
    fetch_job = _jobs(db)[0]
    assert fetch_job.dedupe_key == f"chal:{task.id}:fetch:1"
    assert fetch_job.kind == "challenge_fetch"

    caplog.set_level("INFO")
    _run(handle_challenge_job(db, fetch_job))
    deps = next(job for job in _jobs(db) if job.kind == "challenge_deps")
    assert deps.dedupe_key == f"chal:{task.id}:deps:1"
    _run(handle_challenge_job(db, deps))
    baseline = next(job for job in _jobs(db) if job.kind == "challenge_baseline")
    assert baseline.dedupe_key == f"chal:{task.id}:baseline:1"
    _run(handle_challenge_job(db, baseline))
    attempt_job = next(job for job in _jobs(db) if job.kind == "challenge_attempt")
    attempt_id = attempt_job.payload["attempt_id"]
    assert attempt_job.dedupe_key == f"chal:{task.id}:attempt:{attempt_id}:1"
    db.sync.expire_all()
    assert db.sync.get(ChallengeTask, task.id).state == "open"

    bundle = tmp_path / "bundle.tar"
    _tar(bundle, {"challenge_tests/test_gap.py": b"def test_gap():\n    assert False\n"})
    attempt_job.payload = {
        **attempt_job.payload,
        "claims": [_claim_in().model_dump(mode="json")],
        "bundle_path": str(bundle),
    }
    _run(handle_challenge_job(db, attempt_job))
    validate_job = next(job for job in _jobs(db) if job.kind == "challenge_validate")
    claim_id = validate_job.payload["claim_id"]
    assert validate_job.dedupe_key == f"chal:{task.id}:validate:{claim_id}:1"
    challenger = db.sync.scalar(
        select(ChallengeParticipant).where(ChallengeParticipant.role == "challenger")
    )
    assert challenger.operator_id == "platform:challenger"
    assert challenger.artifacts_seen
    assert all(
        item.startswith("contract:")
        or item.startswith("diff:")
        or db.sync.get(ChallengeArtifact, uuid.UUID(item)).kind == "source_tarball"
        for item in challenger.artifacts_seen
    )
    attempt_jobs = [job for job in calls if _purpose(job.execution_id) == "attempt"]
    assert attempt_jobs and attempt_jobs[0].fixtures_digest is None
    assert attempt_jobs[0].observers == []

    _run(handle_challenge_job(db, validate_job))
    _run(handle_challenge_job(db, validate_job))
    assert captured["tamper_clean"] is True
    assert (
        captured["reference"].execution_id.endswith(":reference:1")
        or ":reference:" in captured["reference"].execution_id
    )
    assert _purpose(captured["target"].execution_id) == "target"
    assert _purpose(captured["positive"].execution_id) == "positive"
    assert _purpose(captured["negative"].execution_id) == "negative"
    assert _purpose(captured["base"].execution_id) == "base"
    assert _purpose(captured["rederivation"].execution_id) == "rederive"
    assert [_purpose(item.execution_id) for item in captured["reruns"]] == [
        "rerun-1",
        "rerun-2",
        "rerun-3",
    ]
    assert _purpose(captured["minimized"].execution_id) == "minimized"
    assert captured["claim"].failure_class == "integrity"
    rederive = next(job for job in calls if _purpose(job.execution_id) == "rederive")
    assert rederive.challenger_bundle is None
    target = next(job for job in calls if _purpose(job.execution_id) == "target")
    assert target.observers == ["authorization_canary"]
    assert target.challenger_bundle is not None

    db.sync.expire_all()
    fresh = db.sync.get(ChallengeTask, task.id)
    assert fresh.state == "closed"
    verdicts = list(db.sync.scalars(select(ChallengeVerdict)).all())
    claim_verdicts = [row for row in verdicts if row.scope == "claim"]
    task_verdicts = [row for row in verdicts if row.scope == "task"]
    assert len(claim_verdicts) == 1
    assert claim_verdicts[0].verdict == "confirmed"
    assert "secure" not in claim_verdicts[0].reason.casefold()
    assert [row.verdict for row in task_verdicts] == ["confirmed"]
    from app.services import challenge_report

    report = _run(challenge_report.build_report(db, task.id))
    assert report.task_verdict == "confirmed"
    assert [item.verdict for item in report.claims] == ["confirmed"]
    finding = db.sync.scalar(select(Finding))
    claim = db.sync.get(ChallengeClaim, uuid.UUID(claim_id))
    assert finding.dedupe_key == f"chal:acme/app:integrity:{claim.claim_hash}"
    assert finding.status == "needs_decision"
    usages = list(db.sync.scalars(select(ExecutionUsage)).all())
    by_id = {row.execution_id: row.cause for row in usages}
    assert by_id[captured["positive"].execution_id] == "control"
    assert by_id[captured["rederivation"].execution_id] == "rederivation"
    assert by_id[captured["reruns"][0].execution_id] == "rerun"
    reservations = list(db.sync.scalars(select(SpendReservation)).all())
    assert reservations
    assert all(row.status == "reconciled" for row in reservations)
    report = next(job for job in _jobs(db) if job.kind == "challenge_report")
    assert report.dedupe_key == f"chal:{task.id}:report:1"
    blob = json.dumps(
        {
            "payloads": [job.payload for job in _jobs(db)],
            "reasons": [fresh.state_reason],
            "meta": [row.meta for row in db.sync.scalars(select(ChallengeArtifact)).all()],
        },
        default=str,
    )
    assert SECRET not in blob
    assert SECRET not in caplog.text


def test_platform_retry_uses_a_new_uncharged_id(db, monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    calls = []

    def runner(job, artifact_root=None):
        calls.append(job)
        status = "sandbox_error" if len(calls) == 1 else "ok"
        return _result(job, status=status)

    _patch_io(monkeypatch, runner=runner, fetch=_fetch(tmp_path))
    task = _run(_make_task(db, budget_cents=100))
    _budget(db, task.owner_user_id)
    task = _run(freeze_contract(db, task.id))
    _run(handle_challenge_job(db, _jobs(db)[0]))
    source = db.sync.scalar(select(ChallengeArtifact))
    task = _force_state(db, task, "open")
    contract = db.sync.get(ChallengeContract, task.current_contract_id)
    attempt_id = uuid.uuid4()
    job = GitHubJob(
        id=uuid.uuid4(),
        kind="challenge_attempt",
        payload={
            "task_id": str(task.id),
            "contract_id": str(contract.id),
            "contract_version": contract.version,
            "attempt_id": str(attempt_id),
        },
        status="running",
        dedupe_key=f"chal:{task.id}:attempt:{attempt_id}:1",
    )
    db.sync.add(job)
    db.sync.flush()
    assert source is not None

    _run(handle_challenge_job(db, job))

    usages = list(db.sync.scalars(select(ExecutionUsage)).all())
    attempt_rows = [row for row in usages if ":attempt:" in row.execution_id]
    assert len(attempt_rows) == 2
    assert attempt_rows[0].execution_id != attempt_rows[1].execution_id
    assert {row.cause for row in attempt_rows} == {"platform_retry"}
    assert all(row.charged is False for row in attempt_rows)
    db.sync.expire_all()
    assert db.sync.get(ChallengeTask, task.id).state == "closed"
    verdict = db.sync.scalar(select(ChallengeVerdict))
    assert verdict.verdict == "no_failure_found"
    assert "No failure found under the stated property, environment, and budget." in verdict.reason


@pytest.mark.parametrize(
    "kind",
    ["exceeded", "paused", "missing"],
)
def test_budget_failure_stops_before_a_run(db, monkeypatch, tmp_path, kind):
    _enable(monkeypatch, tmp_path)
    calls = []

    def runner(job, artifact_root=None):
        calls.append(job)
        return _result(job)

    _patch_io(monkeypatch, runner=runner)
    cents = 500 if kind == "exceeded" else 100
    task = _run(_make_task(db, budget_cents=cents))
    if kind == "exceeded":
        _budget(db, task.owner_user_id, per_job=10)
    elif kind == "paused":
        _budget(db, task.owner_user_id, paused=True)
    task = _run(freeze_contract(db, task.id))
    task = _force_state(db, task, "open")
    contract = db.sync.get(ChallengeContract, task.current_contract_id)
    job = GitHubJob(
        id=uuid.uuid4(),
        kind="challenge_attempt",
        payload={
            "task_id": str(task.id),
            "contract_id": str(contract.id),
            "contract_version": 1,
            "attempt_id": str(uuid.uuid4()),
        },
        status="running",
    )
    _run(handle_challenge_job(db, job))
    db.sync.expire_all()
    fresh = db.sync.get(ChallengeTask, task.id)
    assert fresh.state == "inconclusive"
    assert "budget" in (fresh.state_reason or "")
    assert calls == []


def test_budget_exhaustion_stops_the_verifier_pass(db, monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    calls = []

    def runner(job, artifact_root=None):
        calls.append(job)
        return _result(job, cpu=1)

    decided = []

    def decide(**kwargs):
        decided.append(kwargs)
        return _confirmed()

    _patch_io(monkeypatch, runner=runner, decide=decide)
    task, claim, job = _run(_validating_world(db, budget_cents=1))
    _budget(db, task.owner_user_id, period=100, per_job=100)

    _run(handle_challenge_job(db, job))

    assert len(calls) == 1
    assert _purpose(calls[0].execution_id) == "target"
    assert decided == []
    db.sync.expire_all()
    fresh = db.sync.get(ChallengeTask, task.id)
    assert fresh.state == "inconclusive"
    assert "budget exhausted" in (fresh.state_reason or "")
    assert db.sync.get(ChallengeClaim, claim.id).state == "decided"
    verdict = db.sync.scalar(select(ChallengeVerdict))
    assert verdict.verdict == "inconclusive"


def test_tamper_and_citation_precheck(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", False)
    fixture = tmp_path / "fixtures.bin"
    fixture.write_bytes(b"frozen-fixtures")
    task = _run(_make_task(db))
    task.environment = {"fixtures_path": str(fixture), "python": "3.11"}
    db.sync.flush()
    task = _run(freeze_contract(db, task.id))
    for state in ("baselining", "open", "attempting"):
        task = _run(transition(db, task, state, state))
    contract = db.sync.get(ChallengeContract, task.current_contract_id)
    participant = ChallengeParticipant(
        id=uuid.uuid4(),
        task_id=task.id,
        role="challenger",
        operator_id="platform:challenger",
        platform_operated=True,
        conflict_declaration={},
        artifacts_seen=[f"contract:{contract.id}"],
    )
    attempt = ChallengeAttempt(
        id=uuid.uuid4(),
        task_id=task.id,
        participant_id=participant.id,
        contract_id=contract.id,
        state="running",
    )
    db.sync.add(participant)
    db.sync.add(attempt)
    db.sync.flush()
    good = tmp_path / "good.tar"
    bad = tmp_path / "bad.tar"
    _tar(good, {"challenge_tests/test_gap.py": b"def test_gap():\n    pass\n"})
    _tar(bad, {"app/evil.py": b"print(1)\n"})

    def precheck(claim, content):
        return (False, "missing") if claim.observed == "bad-citation" else (True, "ok")

    monkeypatch.setattr("app.services.challenge_validity.precheck_citation", precheck)

    rejected = _run(
        submit_claim(
            db,
            task_id=task.id,
            attempt_id=attempt.id,
            claim=_claim_in(observed="bad-citation"),
        )
    )
    assert rejected.state == "rejected_precheck"
    early = db.sync.scalar(select(ChallengeVerdict).where(ChallengeVerdict.claim_id == rejected.id))
    assert early.verdict == "invalid_or_duplicate"
    assert early.evidence["citation_valid"] is False

    _attach_bundle(db, task, attempt, bad)
    tampered = _run(submit_claim(db, task_id=task.id, attempt_id=attempt.id, claim=_claim_in()))
    assert tampered.state == "tamper_rejected"
    tamper_verdict = db.sync.scalar(
        select(ChallengeVerdict).where(ChallengeVerdict.claim_id == tampered.id)
    )
    assert tamper_verdict.verdict == "invalid_or_duplicate"
    assert tamper_verdict.evidence["tamper_clean"] is False

    _attach_bundle(db, task, attempt, good)
    fixture.write_bytes(b"changed-fixtures")
    digested = _run(
        submit_claim(
            db,
            task_id=task.id,
            attempt_id=attempt.id,
            claim=_claim_in(observed="fixture drift"),
        )
    )
    assert digested.state == "tamper_rejected"
    assert "fixtures" in db.sync.scalar(
        select(ChallengeVerdict.reason).where(ChallengeVerdict.claim_id == digested.id)
    )

    fixture.write_bytes(b"frozen-fixtures")
    task = _force_state(db, task, "attempting")
    clean = _run(
        submit_claim(
            db,
            task_id=task.id,
            attempt_id=attempt.id,
            claim=_claim_in(observed="clean"),
        )
    )
    assert clean.state == "validating"


def test_no_observer_goes_to_adjudication_without_a_run(db, monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    calls = []

    def runner(job, artifact_root=None):
        calls.append(job)
        return _result(job)

    decided = []
    _patch_io(
        monkeypatch,
        runner=runner,
        decide=lambda **kwargs: decided.append(kwargs) or _confirmed(),
    )
    task, claim, job = _run(_validating_world(db, observers=[]))

    _run(handle_challenge_job(db, job))

    assert calls == []
    assert decided == []
    db.sync.expire_all()
    assert db.sync.get(ChallengeClaim, claim.id).state == "adjudicating"
    assert db.sync.get(ChallengeTask, task.id).state == "adjudicating"


def test_cancel_releases_reservations_and_open_jobs(db, monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)

    def runner(job, artifact_root=None):
        return _result(job)

    _patch_io(monkeypatch, runner=runner, fetch=_fetch(tmp_path))
    task = _run(_make_task(db, budget_cents=100))
    budget = _budget(db, task.owner_user_id)
    task = _run(freeze_contract(db, task.id))
    fetch_job = _jobs(db)[0]
    _run(handle_challenge_job(db, fetch_job))
    fetch_job.status = "done"
    deps = next(job for job in _jobs(db) if job.kind == "challenge_deps")
    _run(handle_challenge_job(db, deps))
    other = GitHubJob(
        id=uuid.uuid4(),
        kind="installation",
        payload={},
        status="pending",
        dedupe_key="other:job",
    )
    db.sync.add(other)
    db.sync.flush()

    cancelled = _run(cancel_task(db, task.id, "operator stop"))

    assert cancelled.state == "cancelled"
    db.sync.expire_all()
    reservation = db.sync.scalar(select(SpendReservation))
    assert reservation.status == "released"
    fresh_budget = db.sync.get(AccountBudget, budget.id)
    assert fresh_budget.reserved_cents == 0
    assert fresh_budget.spent_cents == reservation.used_cents
    for job in _jobs(db):
        if job.dedupe_key and job.dedupe_key.startswith(f"chal:{task.id}:"):
            if job.id == fetch_job.id:
                assert job.status == "done"
            else:
                assert job.status == "cancelled"
    assert db.sync.get(GitHubJob, other.id).status == "pending"
    with pytest.raises(InvalidTransition):
        _run(cancel_task(db, task.id, "again"))


def test_adjudicator_conflict_and_decision(db):
    task = _run(_make_task(db))
    task = _run(freeze_contract(db, task.id))
    contract = db.sync.get(ChallengeContract, task.current_contract_id)
    _run(
        _record_participant(
            db,
            task,
            role="author",
            operator_id="maintainer",
            artifacts_seen=[f"contract:{contract.id}"],
        )
    )
    participant = db.sync.scalar(select(ChallengeParticipant))
    attempt = ChallengeAttempt(
        id=uuid.uuid4(),
        task_id=task.id,
        participant_id=participant.id,
        contract_id=contract.id,
        state="submitted",
    )
    claim = ChallengeClaim(
        id=uuid.uuid4(),
        task_id=task.id,
        attempt_id=attempt.id,
        contract_id=contract.id,
        failure_class="integrity",
        scenario=_claim_in().scenario.model_dump(),
        oracle_citation=_claim_in().oracle_citation.model_dump(),
        expected="hidden",
        observed="listed",
        claimed_impact="cross-tenant read",
        state="adjudicating",
        claim_hash="ef" * 32,
    )
    db.sync.add(attempt)
    db.sync.add(claim)
    db.sync.flush()
    task = _force_state(db, task, "adjudicating")
    with pytest.raises(RoleConflict):
        _run(
            _record_adjudication(
                db,
                task_id=task.id,
                claim_id=claim.id,
                operator_id="maintainer",
                verdict="confirmed",
                reason="same person",
            )
        )
    assert db.sync.get(ChallengeClaim, claim.id).state == "adjudicating"
    with pytest.raises(ValueError):
        _run(
            _record_adjudication(
                db,
                task_id=task.id,
                claim_id=claim.id,
                operator_id=str(uuid.uuid4()),
                verdict="confirmed",
                reason="the code is Secure here",
            )
        )
    assert db.sync.get(ChallengeClaim, claim.id).state == "adjudicating"
    assert list(db.sync.scalars(select(ChallengeVerdict)).all()) == []
    _run(
        _record_adjudication(
            db,
            task_id=task.id,
            claim_id=claim.id,
            operator_id=str(uuid.uuid4()),
            verdict="confirmed",
            reason="controls held",
        )
    )
    db.sync.expire_all()
    assert db.sync.get(ChallengeClaim, claim.id).state == "decided"
    assert db.sync.get(ChallengeTask, task.id).state == "closed"
    verdicts = list(db.sync.scalars(select(ChallengeVerdict)).all())
    claim_verdicts = [row for row in verdicts if row.scope == "claim"]
    assert len(claim_verdicts) == 1
    assert claim_verdicts[0].decided_by == "adjudicator"
    assert claim_verdicts[0].verdict == "confirmed"
    assert claim_verdicts[0].reason == "controls held"
    assert [row.verdict for row in verdicts if row.scope == "task"] == ["confirmed"]


def _attach_bundle(db, task, attempt, path):
    data = path.read_bytes()
    for row in db.sync.scalars(
        select(ChallengeArtifact).where(ChallengeArtifact.kind == "challenger_test")
    ).all():
        if str((row.meta or {}).get("attempt_id")) == str(attempt.id):
            db.sync.delete(row)
    db.sync.flush()
    row = ChallengeArtifact(
        id=uuid.uuid4(),
        task_id=task.id,
        tenant_user_id=task.owner_user_id,
        kind="challenger_test",
        content_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        storage_path=str(path),
        meta={"attempt_id": str(attempt.id), "role": "challenger_bundle"},
    )
    db.sync.add(row)
    db.sync.flush()
    return row


async def _validating_world(db, *, budget_cents=100, observers=None):
    task = await _make_task(db, budget_cents=budget_cents)
    content = {
        "property": PROPERTY,
        "failure_classes": ["integrity"],
        "oracle_sources": ["property_clause"],
        "observers": ["authorization_canary"] if observers is None else observers,
        "environment": {"python": "3.11", "reference_digest": REF},
        "budget_cents": budget_cents,
        "deadline_at": WHEN.isoformat(),
        "base_sha": BASE,
        "target_sha": HEAD,
        "fixtures_digest": None,
        "observers_digest": None,
    }
    if observers == []:
        content["environment"] = {"python": "3.11"}
    contract = ChallengeContract(
        id=uuid.uuid4(),
        task_id=task.id,
        version=1,
        content_hash=spec_content_hash(content),
        content=content,
    )
    db.add(contract)
    task.current_contract_id = contract.id
    task.state = "validating"
    participant = ChallengeParticipant(
        id=uuid.uuid4(),
        task_id=task.id,
        role="challenger",
        operator_id="platform:challenger",
        platform_operated=True,
        conflict_declaration={},
        artifacts_seen=[f"contract:{contract.id}"],
    )
    attempt = ChallengeAttempt(
        id=uuid.uuid4(),
        task_id=task.id,
        participant_id=participant.id,
        contract_id=contract.id,
        state="submitted",
    )
    claim_body = _claim_in()
    claim = ChallengeClaim(
        id=uuid.uuid4(),
        task_id=task.id,
        attempt_id=attempt.id,
        contract_id=contract.id,
        failure_class=claim_body.failure_class,
        scenario=claim_body.scenario.model_dump(),
        oracle_citation=claim_body.oracle_citation.model_dump(),
        expected=claim_body.expected,
        observed=claim_body.observed,
        claimed_impact=claim_body.claimed_impact,
        state="validating",
        claim_hash="ab" * 32,
    )
    for role, sha in (("target", HEAD), ("base", BASE)):
        data = sha.encode()
        db.add(
            ChallengeArtifact(
                id=uuid.uuid4(),
                task_id=task.id,
                tenant_user_id=task.owner_user_id,
                kind="source_tarball",
                content_hash=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                storage_path=f"/tmp/{sha}.tar",
                meta={"role": role, "sha": sha},
            )
        )
    bundle = ChallengeArtifact(
        id=uuid.uuid4(),
        task_id=task.id,
        tenant_user_id=task.owner_user_id,
        kind="challenger_test",
        content_hash="ef" * 32,
        size_bytes=4,
        storage_path="",
        meta={"attempt_id": str(attempt.id), "role": "challenger_bundle"},
    )
    db.add(participant)
    db.add(attempt)
    db.add(claim)
    db.add(bundle)
    await db.flush()
    job = GitHubJob(
        id=uuid.uuid4(),
        kind="challenge_validate",
        payload={
            "task_id": str(task.id),
            "contract_id": str(contract.id),
            "contract_version": 1,
            "claim_id": str(claim.id),
            "attempt_id": str(attempt.id),
        },
        status="running",
        dedupe_key=f"chal:{task.id}:validate:{claim.id}:1",
    )
    db.add(job)
    await db.flush()
    return task, claim, job
