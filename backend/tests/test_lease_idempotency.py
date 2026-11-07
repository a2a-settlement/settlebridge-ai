"""Idempotent lease claim and submit, and a raced settlement insert. SQLite only."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.models.github import (
    AcceptanceSpec,
    GitHubBountyBinding,
    GitHubIdentity,
    GitHubInstallation,
    GitHubJob,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
    GitHubWebhookDelivery,
    JobKind,
    JobStatus,
)
from app.models.user import User, UserType
from app.routes.github_leases import router
from app.services import github_app, github_leases, trust_gateway
from app.services.github_bounty_service import (
    get_settlement_for_binding,
    handle_pull_request_closed,
)
from app.services.trust_gateway import AgentClaims

REPO = "acme/widget"
AGENT = "solver-agent"
SOLVER = uuid.UUID("22222222-2222-4222-b222-222222222222")
FIXED = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
CLAIM_KEY = "claim-key-1"
SUBMIT_KEY = "submit-key-1"
CLAIMS = AgentClaims(
    agent_id=AGENT,
    org_id="org-1",
    kya_level=3,
    reputation=0.95,
    scopes=frozenset({trust_gateway.READ_SCOPE}),
    raw={},
)


def _run(coro):
    return asyncio.run(coro)


class _Nested:
    def __init__(self, sync: Session):
        self._tx = None
        self._sync = sync

    async def __aenter__(self):
        self._tx = self._sync.begin_nested()
        return self._tx.__enter__()

    async def __aexit__(self, exc_type, exc, tb):
        return self._tx.__exit__(exc_type, exc, tb)


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    async def get(self, model, ident, **kwargs):
        return self.sync.get(model, ident, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()

    async def flush(self):
        self.sync.flush()

    def begin_nested(self):
        return _Nested(self.sync)

    def expunge(self, obj):
        self.sync.expunge(obj)


def _engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    User.__table__.create(eng)
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE bounties ("
                "id CHAR(32) NOT NULL PRIMARY KEY, "
                "title VARCHAR(500) NOT NULL, "
                "status VARCHAR(32) NOT NULL)"
            )
        )
    for model in (
        GitHubIdentity,
        GitHubInstallation,
        GitHubRepoLink,
        GitHubBountyBinding,
        AcceptanceSpec,
        GitHubLease,
        GitHubWebhookDelivery,
        GitHubJob,
        GitHubSettlement,
    ):
        model.__table__.create(eng)
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_github_leases_agent_claim_key "
                "ON github_leases (agent_id, claim_idempotency_key) "
                "WHERE claim_idempotency_key IS NOT NULL"
            )
        )
    return eng


def _bridge(eng) -> _Bridge:
    return _Bridge(Session(eng, expire_on_commit=False))


def _client_for(bridge: _Bridge) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _insert_bounty(bridge: _Bridge, bounty_id: uuid.UUID, title: str) -> None:
    bridge.sync.execute(
        text("INSERT INTO bounties (id, title, status) VALUES (:id, :title, :status)"),
        {"id": bounty_id.hex, "title": title, "status": "OPEN"},
    )


def _add_bounty(bridge: _Bridge, issue_number: int, **binding_overrides):
    bounty_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    _insert_bounty(bridge, bounty_id, f"Issue {issue_number}")
    binding = {
        "id": binding_id,
        "bounty_id": bounty_id,
        "repo_full_name": REPO,
        "issue_number": issue_number,
        "current_spec_id": spec_id,
        "state": "advertised",
        "created_at": FIXED,
        "updated_at": FIXED,
    }
    binding.update(binding_overrides)
    bridge.sync.add(GitHubBountyBinding(**binding))
    bridge.sync.add(
        AcceptanceSpec(
            id=spec_id,
            binding_id=binding_id,
            version=1,
            content_hash="ab" * 32,
            test_command="pytest -q",
            required_checks=[{"name": "ci", "app_id": 1}],
            policy={},
            payment_trigger="merge_with_checks",
            amount_ate=1000,
            fee_bps=0,
            required_kya=1,
            required_ema=0.5,
            security_tier=False,
            created_at=FIXED,
        )
    )
    return bounty_id, binding_id, spec_id


def _seed(bridge: _Bridge) -> dict:
    bridge.sync.add(
        User(
            id=SOLVER,
            email="solver@example.com",
            display_name="Solver",
            user_type=UserType.AGENT_OPERATOR,
            exchange_bot_id=AGENT,
        )
    )
    bridge.sync.add(
        GitHubInstallation(
            id=uuid.uuid4(),
            installation_id=4242,
            account_login="acme",
        )
    )
    bridge.sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=4242,
            repo_full_name=REPO,
            enabled=True,
            default_policy={},
            required_checks=[],
            visibility="public",
        )
    )
    bridge.sync.add(
        GitHubIdentity(
            id=uuid.uuid4(),
            user_id=SOLVER,
            github_user_id=4242,
            login="solver",
            verified_at=FIXED,
        )
    )
    bounty_a, binding_a, spec_a = _add_bounty(bridge, 12)
    bounty_b, binding_b, spec_b = _add_bounty(bridge, 13)
    bridge.sync.commit()
    return {
        "bounty_a": bounty_a,
        "binding_a": binding_a,
        "spec_a": spec_a,
        "bounty_b": bounty_b,
        "binding_b": binding_b,
        "spec_b": spec_b,
    }


def _headers(*, lease: str | None = None, key: str | None = None) -> dict:
    headers = {"Authorization": "Bearer good-token"}
    if lease is not None:
        headers["X-Lease-Token"] = lease
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _shown(response) -> tuple:
    try:
        body = response.json()
    except Exception:
        return (response.status_code,)
    if isinstance(body, dict):
        body = {key: value for key, value in body.items() if key != "lease_token"}
    return response.status_code, body


def _leases(bridge: _Bridge) -> list:
    return list(bridge.sync.scalars(select(GitHubLease)).all())


def _jobs(bridge: _Bridge, kind: str | None = None) -> list:
    rows = list(bridge.sync.scalars(select(GitHubJob)).all())
    if kind is None:
        return rows
    return [row for row in rows if row.kind == kind]


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_ENFORCE_KYA_LINEAGE_SEPARATION", False)
    monkeypatch.setattr(settings, "GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM", False)
    monkeypatch.setattr(github_leases, "_now", lambda: FIXED)

    async def _no_lock(db, agent_id):
        return None

    monkeypatch.setattr(github_leases, "lock_agent_for_claim", _no_lock)

    def bearer(header):
        if not isinstance(header, str) or not header.startswith("Bearer "):
            raise trust_gateway.TrustTokenError("missing")
        token = header[7:].strip()
        if token == "":
            raise trust_gateway.TrustTokenError("missing")
        return token

    def verify(token):
        if token == "bad":
            raise trust_gateway.TrustTokenError("bad")
        return CLAIMS

    monkeypatch.setattr(trust_gateway, "bearer_token", bearer)
    monkeypatch.setattr(trust_gateway, "verify_gateway_token", verify)
    eng = _engine()
    bridge = _bridge(eng)
    seeded = _seed(bridge)
    client = _client_for(bridge)
    try:
        yield {"client": client, "bridge": bridge, **seeded}
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _claim(world, bounty_id, key=None):
    return world["client"].post(
        f"/api/v1/github/bounties/{bounty_id}/claim-lease",
        headers=_headers(key=key),
    )


def _renew(world, lease_id, token):
    return world["client"].post(
        f"/api/v1/github/leases/{lease_id}/renew",
        headers=_headers(lease=token),
    )


def _release(world, lease_id, token):
    return world["client"].post(
        f"/api/v1/github/leases/{lease_id}/release",
        headers=_headers(lease=token),
    )


def _submit(world, lease_id, token, body, key=None):
    return world["client"].post(
        f"/api/v1/github/leases/{lease_id}/submit",
        headers=_headers(lease=token, key=key),
        json=body,
    )


def _pull(number: int = 7) -> dict:
    return {
        "state": "open",
        "number": number,
        "user": {"id": 4242, "login": "solver"},
        "body": "Fixes #12",
        "head": {"sha": HEAD},
        "created_at": "2026-06-01T12:00:00Z",
    }


def test_claim_replay_rotates_the_token_and_rejects_the_old_one(world):
    first = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert first.status_code == 201, _shown(first)
    original = first.json()
    again = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert again.status_code == 200, _shown(again)
    replay = again.json()
    assert replay["lease_id"] == original["lease_id"]
    assert replay["lease_token"] != original["lease_token"]
    assert replay["bounty_id"] == original["bounty_id"]
    assert replay["spec"] == original["spec"]
    assert replay["test_command"] == original["test_command"]
    assert replay["work_expires_at"] == original["work_expires_at"]
    assert replay["hard_expires_at"] == original["hard_expires_at"]
    stored = world["bridge"].sync.get(GitHubLease, uuid.UUID(original["lease_id"]))
    assert stored.token_hash == github_leases.hash_lease_token(replay["lease_token"])
    assert stored.claim_idempotency_key == CLAIM_KEY
    assert len(_leases(world["bridge"])) == 1
    rejected = _renew(world, original["lease_id"], original["lease_token"])
    assert rejected.status_code == 403, _shown(rejected)
    assert rejected.json()["error"] == "not_lease_holder"
    renewed = _renew(world, original["lease_id"], replay["lease_token"])
    assert renewed.status_code == 200, _shown(renewed)
    assert renewed.json()["lease_id"] == original["lease_id"]


def test_claim_key_reused_on_another_bounty_is_409(world):
    first = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert first.status_code == 201, _shown(first)
    reused = _claim(world, world["bounty_b"], key=CLAIM_KEY)
    assert reused.status_code == 409, _shown(reused)
    assert reused.json()["error"] == "idempotency_key_reused"
    assert len(_leases(world["bridge"])) == 1


def test_claim_replay_after_release_is_409_with_final_status(world):
    first = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert first.status_code == 201, _shown(first)
    token = first.json()["lease_token"]
    lease_id = first.json()["lease_id"]
    released = _release(world, lease_id, token)
    assert released.status_code == 200, _shown(released)
    replay = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert replay.status_code == 409, _shown(replay)
    assert replay.json()["error"] == "idempotency_key_reused"
    assert replay.json()["status"] == "released"
    assert len(_leases(world["bridge"])) == 1


@pytest.mark.parametrize("key", ["", "x" * 129])
def test_bad_claim_key_format_is_400(world, key):
    response = _claim(world, world["bounty_a"], key=key)
    assert response.status_code == 400, _shown(response)
    assert response.json()["error"] == "invalid_idempotency_key"
    assert "detail" not in response.json()
    if key:
        assert key not in response.text
    assert _leases(world["bridge"]) == []


def test_non_ascii_claim_key_is_400(world):
    for key in ("caf\u00e9", "bad\nkey", "bad\tkey", "bad\x7fkey"):
        result = _run(github_leases.claim_lease(world["bridge"], world["bounty_a"], CLAIMS, key))
        assert result.status_code == 400
        assert result.body["error"] == "invalid_idempotency_key"
        assert list(result.body) == ["error"]
    assert _leases(world["bridge"]) == []


def test_claim_integrity_error_race_returns_the_replay(world, monkeypatch):
    first = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert first.status_code == 201, _shown(first)
    original = first.json()
    real_key = github_leases._lease_for_claim_key
    real_held = github_leases.fetch_active_lease_for_binding
    pending = {"key": 1, "held": 1}

    async def miss_key(db, agent_id, key):
        if pending["key"]:
            pending["key"] -= 1
            return None
        return await real_key(db, agent_id, key)

    async def miss_held(db, binding_id):
        if pending["held"]:
            pending["held"] -= 1
            return None
        return await real_held(db, binding_id)

    monkeypatch.setattr(github_leases, "_lease_for_claim_key", miss_key)
    monkeypatch.setattr(github_leases, "fetch_active_lease_for_binding", miss_held)
    raced = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert raced.status_code == 200, _shown(raced)
    body = raced.json()
    assert body["lease_id"] == original["lease_id"]
    assert body["work_expires_at"] == original["work_expires_at"]
    assert body["spec"] == original["spec"]
    assert pending == {"key": 0, "held": 0}
    assert len(_leases(world["bridge"])) == 1
    rejected = _renew(world, original["lease_id"], original["lease_token"])
    assert rejected.status_code == 403, _shown(rejected)
    renewed = _renew(world, original["lease_id"], body["lease_token"])
    assert renewed.status_code == 200, _shown(renewed)


def test_submit_replay_returns_the_same_result_without_a_second_side_effect(world, monkeypatch):
    calls = []

    async def get_pull_request(installation_id, repo, number):
        calls.append((installation_id, repo, number))
        return _pull(number)

    monkeypatch.setattr(github_app, "get_pull_request", get_pull_request)
    claimed = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert claimed.status_code == 201, _shown(claimed)
    lease_id = claimed.json()["lease_id"]
    token = claimed.json()["lease_token"]
    payload = {
        "pr_url": f"https://github.com/{REPO}/pull/7",
        "otel_trace_id": "t" * 80,
    }
    first = _submit(world, lease_id, token, payload, key=SUBMIT_KEY)
    assert first.status_code == 200, _shown(first)
    again = _submit(world, lease_id, token, payload, key=SUBMIT_KEY)
    assert again.status_code == 200, _shown(again)
    assert again.json() == first.json()
    assert calls == [(4242, REPO, 7)]
    assert len(_jobs(world["bridge"])) == 1
    stored = world["bridge"].sync.get(GitHubLease, uuid.UUID(lease_id))
    assert stored.submit_idempotency_key == SUBMIT_KEY
    assert stored.status == "submitted"
    assert stored.otel_trace_id == "t" * 64
    other_key = _submit(world, lease_id, token, payload, key="submit-key-2")
    assert other_key.status_code == 409, _shown(other_key)
    assert other_key.json()["error"] == "lease_not_active"
    assert calls == [(4242, REPO, 7)]


def test_submit_same_key_different_body_is_409(world, monkeypatch):
    calls = []

    async def get_pull_request(installation_id, repo, number):
        calls.append(number)
        return _pull(number)

    monkeypatch.setattr(github_app, "get_pull_request", get_pull_request)
    claimed = _claim(world, world["bounty_a"], key=CLAIM_KEY)
    assert claimed.status_code == 201, _shown(claimed)
    lease_id = claimed.json()["lease_id"]
    token = claimed.json()["lease_token"]
    payload = {"pr_url": f"https://github.com/{REPO}/pull/7", "otel_trace_id": "trace-a"}
    first = _submit(world, lease_id, token, payload, key=SUBMIT_KEY)
    assert first.status_code == 200, _shown(first)
    changed = _submit(
        world,
        lease_id,
        token,
        {"pr_url": f"https://github.com/{REPO}/pull/8", "otel_trace_id": "trace-a"},
        key=SUBMIT_KEY,
    )
    assert changed.status_code == 409, _shown(changed)
    assert changed.json()["error"] == "idempotency_body_mismatch"
    traced = _submit(
        world,
        lease_id,
        token,
        {"pr_url": f"https://github.com/{REPO}/pull/7", "otel_trace_id": "trace-b"},
        key=SUBMIT_KEY,
    )
    assert traced.status_code == 409, _shown(traced)
    assert traced.json()["error"] == "idempotency_body_mismatch"
    assert calls == [7]
    assert len(_jobs(world["bridge"])) == 1
    stored = world["bridge"].sync.get(GitHubLease, uuid.UUID(lease_id))
    assert stored.pr_number == 7
    assert stored.otel_trace_id == "trace-a"


def test_settlement_insert_race_leaves_one_settlement_and_one_advance_job(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    eng = _engine()
    bridge = _bridge(eng)
    try:
        bridge.sync.add(
            User(
                id=SOLVER,
                email="solver@example.com",
                display_name="Solver",
                user_type=UserType.AGENT_OPERATOR,
                exchange_bot_id=AGENT,
            )
        )
        _bounty_id, binding_id, spec_id = _add_bounty(
            bridge,
            12,
            pr_number=7,
            state="awaiting_merge",
            evaluated_head_sha=HEAD,
            last_assessment={"outcome": "ACCEPTABLE", "findings": []},
        )
        binding = bridge.sync.get(GitHubBountyBinding, binding_id)
        binding.evaluated_spec_id = spec_id
        lease_id = uuid.uuid4()
        bridge.sync.add(
            GitHubLease(
                id=lease_id,
                binding_id=binding_id,
                agent_id=AGENT,
                user_id=SOLVER,
                github_user_id=4242,
                spec_id=spec_id,
                token_hash="b" * 64,
                status="submitted",
                work_expires_at=FIXED,
                hard_expires_at=FIXED + timedelta(hours=2),
                hold_expires_at=datetime.now(timezone.utc) + timedelta(days=2),
                pr_number=7,
                created_at=FIXED,
                updated_at=FIXED,
            )
        )
        settlement_id = uuid.uuid4()
        bridge.sync.add(
            GitHubSettlement(
                id=settlement_id,
                binding_id=binding_id,
                spec_id=spec_id,
                solver_agent_id=AGENT,
                head_sha=HEAD,
                state="pending",
                idempotency_prefix=f"gh:{binding_id}:{spec_id}",
            )
        )
        bridge.sync.add(
            GitHubJob(
                id=uuid.uuid4(),
                kind=JobKind.ADVANCE_SETTLEMENT.value,
                payload={"settlement_id": str(settlement_id)},
                status=JobStatus.PENDING.value,
            )
        )
        bridge.sync.commit()
        calls = {"n": 0}

        async def miss_once(db, wanted):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return await get_settlement_for_binding(db, wanted)

        async def list_check_runs(installation_id, repo, sha):
            return [
                {
                    "name": "ci",
                    "head_sha": sha,
                    "app": {"id": 1},
                    "status": "completed",
                    "conclusion": "success",
                    "id": 1,
                }
            ]

        async def sticky(*args, **kwargs):
            return 11

        monkeypatch.setattr(
            "app.services.github_bounty_service.get_settlement_for_binding",
            miss_once,
        )
        monkeypatch.setattr(github_app, "list_check_runs", list_check_runs)
        monkeypatch.setattr(github_app, "upsert_sticky_comment", sticky)
        job = type("Job", (), {})()
        job.payload = {
            "event": "pull_request",
            "action": "closed",
            "data": {
                "installation": {"id": 4242},
                "repository": {"full_name": REPO},
                "pull_request": {"number": 7, "merged": True, "head": {"sha": HEAD}},
            },
        }
        job.attempts = 0
        _run(handle_pull_request_closed(bridge, job))
        settlements = list(bridge.sync.scalars(select(GitHubSettlement)).all())
        advances = _jobs(bridge, JobKind.ADVANCE_SETTLEMENT.value)
        assert len(settlements) == 1
        assert settlements[0].id == settlement_id
        assert len(advances) == 1
        assert advances[0].payload["settlement_id"] == str(settlement_id)
        assert calls["n"] == 2
    finally:
        bridge.sync.close()
        eng.dispose()
