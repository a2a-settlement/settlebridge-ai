"""Claim-time escrow holds and solver/funder separation. No database and no network."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.github import GitHubJob, GitHubLease, JobKind, JobStatus
from app.services import github_app, github_bounty_service, github_leases, github_settlement
from app.services.github_errors import RetryableJobError
from app.services.github_settlement import SettlementContext

FIXED = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
REPO = "acme/widget"
AGENT = "solver-bot"
FUNDER_AGENT = "funder-bot"
TOKEN = "sbl_holdtoken"
CLAIM_KEYS = {
    "lease_id",
    "lease_token",
    "bounty_id",
    "repo",
    "issue_number",
    "issue_url",
    "spec",
    "test_command",
    "work_expires_at",
    "hard_expires_at",
}


def _run(coro):
    return asyncio.run(coro)


def _http_error(status: int, text: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://exchange.test/v1/exchange/escrow")
    response = httpx.Response(status, request=request, text=text)
    return httpx.HTTPStatusError(f"status {status}", request=request, response=response)


class _DB:
    def __init__(self, users=None):
        self.users = dict(users or {})
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.commit_error = None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            error = self.commit_error
            self.commit_error = None
            raise error

    async def rollback(self):
        self.rollbacks += 1

    async def get(self, model, pk):
        return self.users.get(pk)


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


class _Sweep:
    def __init__(self, leases, bindings=None):
        self.leases = leases
        self.bindings = bindings or {}
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        return _Result(self.leases)

    async def get(self, model, pk):
        return self.bindings.get(pk)

    async def commit(self):
        self.committed = True

    def add(self, obj):
        self.added.append(obj)


def _jobs(rows):
    return [obj for obj in rows if isinstance(obj, GitHubJob)]


class _Claim:
    def __init__(self):
        self.solver_id = uuid.uuid4()
        self.funder_id = uuid.uuid4()
        self.spec_id = uuid.uuid4()
        self.binding = types.SimpleNamespace(
            id=uuid.uuid4(),
            bounty_id=uuid.uuid4(),
            repo_full_name=REPO,
            issue_number=12,
            current_spec_id=self.spec_id,
        )
        self.spec = types.SimpleNamespace(
            id=self.spec_id,
            version=1,
            content_hash="ab",
            test_command="pytest -q",
            required_checks=[],
            policy={},
            payment_trigger="merge_with_checks",
            required_kya=0,
            required_ema=0.0,
            amount_ate=100,
        )
        self.solver = types.SimpleNamespace(
            id=self.solver_id, exchange_bot_id=AGENT, exchange_api_key="solver-key"
        )
        self.funder = types.SimpleNamespace(
            id=self.funder_id,
            exchange_bot_id=FUNDER_AGENT,
            exchange_api_key="funder-key",
        )
        self.link = types.SimpleNamespace(
            repo_full_name=REPO,
            funder_user_id=self.funder_id,
            installation_id=9,
            enabled=True,
        )
        self.solver_github = 4242
        self.funder_github = 777
        self.kya = 2
        self.reputation = 0.91
        self.enforce = True
        self.lock = False
        self.balance = 10_000
        self.balance_error = None
        self.create_result = {"escrow_id": "esc-hold"}
        self.create_error = None
        self.principal_error = None
        self.principals = {FUNDER_AGENT: "principal-funder", AGENT: "principal-solver"}
        self.events: list[str] = []
        self.principal_calls: list[str] = []
        self.principal_keys: list[str | None] = []
        self.create_user = None
        self.create_kwargs = None
        self.db = None
        self.bounty_funder = self.funder

    def install(self, monkeypatch):
        world = self
        self.db = _DB({self.solver_id: self.solver, self.funder_id: self.funder})

        async def fetch_advertised_binding(db, bounty_id):
            if bounty_id != world.binding.bounty_id:
                return None
            return world.binding

        async def fetch_spec(db, spec_id):
            if spec_id != world.spec.id:
                return None
            return world.spec

        async def fetch_users_for_agent(db, agent_id):
            return [world.solver]

        async def fetch_identity_for_user(db, user_id):
            if user_id == world.solver_id:
                return types.SimpleNamespace(user_id=user_id, github_user_id=world.solver_github)
            if user_id == world.funder_id:
                return types.SimpleNamespace(user_id=user_id, github_user_id=world.funder_github)
            return None

        async def fetch_repo_link(db, repo_full_name):
            return world.link

        async def lock_agent_for_claim(db, agent_id):
            return None

        async def count_active_leases(db, agent_id):
            return 0

        async def fetch_expired_timestamps(db, agent_id, since):
            return []

        async def fetch_active_lease_for_binding(db, binding_id):
            return None

        def original_add(obj):
            world.db.added.append(obj)
            world.events.append("add")

        world.db.add = original_add

        def get_balance(account_id):
            world.events.append("balance")
            if world.balance_error is not None:
                raise world.balance_error
            return {"available": world.balance}

        def create_escrow(user, **kwargs):
            world.events.append("create")
            world.create_user = user
            world.create_kwargs = kwargs
            if world.create_error is not None:
                raise world.create_error
            return world.create_result

        class _Client:
            def __init__(self, *args, **kwargs):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def fetch_principal(client, agent_id, api_key):
            world.principal_calls.append(agent_id)
            world.principal_keys.append(api_key)
            if world.principal_error is not None:
                raise github_leases._PrincipalUnavailable(str(world.principal_error))
            found = world.principals.get(agent_id)
            if found is None:
                return {}
            if isinstance(found, dict):
                return found
            return {found: 1.0}

        async def fetch_bounty_funder(db, bounty_id):
            assert bounty_id == world.binding.bounty_id
            return world.bounty_funder

        monkeypatch.setattr(settings, "GITHUB_ENFORCE_KYA_LINEAGE_SEPARATION", self.enforce)
        monkeypatch.setattr(settings, "GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM", self.lock)
        monkeypatch.setattr(settings, "GITHUB_EXCHANGE_FEE_PERCENT", "0.25")
        monkeypatch.setattr(settings, "GITHUB_EXCHANGE_MIN_FEE", 1)
        monkeypatch.setattr(github_leases, "fetch_advertised_binding", fetch_advertised_binding)
        monkeypatch.setattr(github_leases, "fetch_spec", fetch_spec)
        monkeypatch.setattr(github_leases, "fetch_users_for_agent", fetch_users_for_agent)
        monkeypatch.setattr(github_leases, "fetch_identity_for_user", fetch_identity_for_user)
        monkeypatch.setattr(github_leases, "fetch_repo_link", fetch_repo_link)
        monkeypatch.setattr(github_leases, "lock_agent_for_claim", lock_agent_for_claim)
        monkeypatch.setattr(github_leases, "count_active_leases", count_active_leases)
        monkeypatch.setattr(github_leases, "fetch_expired_timestamps", fetch_expired_timestamps)
        monkeypatch.setattr(
            github_leases, "fetch_active_lease_for_binding", fetch_active_lease_for_binding
        )
        monkeypatch.setattr(github_leases.exchange, "get_balance_dashboard", get_balance)
        monkeypatch.setattr(github_leases.exchange, "create_escrow", create_escrow)
        monkeypatch.setattr(github_leases.httpx, "AsyncClient", _Client)
        monkeypatch.setattr(github_leases, "_principal_links", fetch_principal)
        monkeypatch.setattr(github_leases, "fetch_bounty_funder", fetch_bounty_funder)
        return self

    def claim(self):
        claims = types.SimpleNamespace(
            agent_id=AGENT, kya_level=self.kya, reputation=self.reputation
        )
        return _run(github_leases.claim_lease(self.db, self.binding.bounty_id, claims))


def _lease_row(world: _Claim):
    rows = [obj for obj in world.db.added if isinstance(obj, GitHubLease)]
    assert len(rows) == 1
    return rows[0]


def test_self_dealing_same_user_is_refused_before_exchange(monkeypatch):
    world = _Claim()
    world.lock = True
    world.install(monkeypatch)
    world.link.funder_user_id = world.solver_id
    result = world.claim()
    assert result.status_code == 403
    assert result.body == {"error": "self_dealing"}
    assert world.events == []
    assert world.principal_calls == []
    assert world.db.added == []


def test_self_dealing_same_github_account_is_refused(monkeypatch):
    world = _Claim()
    world.funder_github = world.solver_github
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 403
    assert result.body == {"error": "self_dealing"}
    assert world.principal_calls == []
    assert world.db.commits == 0


def test_self_dealing_same_principal_is_refused(monkeypatch):
    world = _Claim()
    world.principals = {FUNDER_AGENT: "principal-shared", AGENT: "principal-shared"}
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 403
    assert result.body == {"error": "self_dealing"}
    assert world.principal_calls == [FUNDER_AGENT, AGENT]
    assert "balance" not in world.events


def test_principal_lookup_failure_refuses_the_claim(monkeypatch):
    world = _Claim()
    world.principal_error = RuntimeError("lookup down")
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 502
    assert result.body == {"error": "exchange_unavailable"}
    assert world.principal_calls == [FUNDER_AGENT]
    assert world.db.added == []


def test_self_dealing_checks_the_bounty_funder_when_the_link_funder_changed(monkeypatch):
    world = _Claim()
    world.bounty_funder = types.SimpleNamespace(id=world.solver_id, exchange_bot_id=AGENT)
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 403
    assert result.body == {"error": "self_dealing"}


def test_hold_is_funded_by_the_bounty_requester_not_the_link_funder(monkeypatch):
    world = _Claim()
    world.lock = True
    requester = types.SimpleNamespace(
        id=uuid.uuid4(), exchange_bot_id="requester-bot", exchange_api_key="set"
    )
    world.bounty_funder = requester
    world.principals["requester-bot"] = "principal-requester"
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 201
    assert world.create_user is requester


def test_missing_principal_does_not_refuse(monkeypatch):
    world = _Claim()
    world.principals = {FUNDER_AGENT: None, AGENT: "principal-solver"}
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 201
    assert result.body["lease_id"]
    lease = _lease_row(world)
    assert lease.claim_kya == 2
    assert lease.claim_reputation == 0.91


def test_self_dealing_flag_off_allows_the_funder_to_claim(monkeypatch):
    world = _Claim()
    world.enforce = False
    world.install(monkeypatch)
    world.link.funder_user_id = world.solver_id
    world.funder_github = world.solver_github
    result = world.claim()
    assert result.status_code == 201
    assert world.principal_calls == []
    assert "balance" not in world.events


def test_hold_flag_off_keeps_the_claim_response_and_skips_exchange(monkeypatch):
    world = _Claim()
    world.lock = False
    world.funder.exchange_bot_id = None
    world.install(monkeypatch)

    def forbid(*_args, **_kwargs):
        raise AssertionError("exchange should not be called")

    monkeypatch.setattr(github_leases.exchange, "get_balance_dashboard", forbid)
    monkeypatch.setattr(github_leases.exchange, "create_escrow", forbid)
    result = world.claim()
    assert result.status_code == 201
    assert set(result.body) == CLAIM_KEYS
    assert "funds_reserved" not in result.body
    lease = _lease_row(world)
    assert lease.hold_escrow_id is None
    assert lease.claim_kya == world.kya
    assert lease.claim_reputation == world.reputation
    assert world.db.commits == 1
    assert world.principal_calls == []
    assert world.events == ["add"]


def test_insufficient_balance_includes_the_exchange_fee(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance = 100
    world.install(monkeypatch)
    assert github_bounty_service.escrow_cost(100) == 101
    result = world.claim()
    assert result.status_code == 409
    assert result.body == {"error": "funder_insufficient"}
    assert world.db.added == []
    assert world.db.commits == 0
    assert world.events == ["balance"]


def test_balance_lookup_failure_is_exchange_unavailable(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance_error = _http_error(503, "down")
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 502
    assert result.body == {"error": "exchange_unavailable"}
    assert world.db.added == []
    assert "create" not in world.events


def test_hold_is_created_with_ttl_and_idempotency_key(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance = 101
    world.install(monkeypatch)
    monkeypatch.setattr(settings, "LEASE_HARD_CAP_MINUTES", 10)
    monkeypatch.setattr(settings, "LEASE_SUBMITTED_HOLD_HOURS", 2)
    monkeypatch.setattr(settings, "GITHUB_ALERT_FIXED_TIMEOUT_HOURS", 3)
    monkeypatch.setattr(settings, "GITHUB_HOLD_TTL_MARGIN_MINUTES", 4)
    result = world.claim()
    assert result.status_code == 201
    assert result.body["funds_reserved"] is True
    assert set(result.body) == CLAIM_KEYS | {"funds_reserved"}
    lease = _lease_row(world)
    assert lease.hold_escrow_id == "esc-hold"
    assert lease.status == "work"
    assert lease.claim_kya == 2
    assert lease.claim_reputation == 0.91
    assert world.create_user is world.funder
    assert world.create_kwargs == {
        "provider_id": AGENT,
        "amount": 100,
        "task_id": f"gh-hold:{lease.id}",
        "ttl_minutes": 10 + 2 * 60 + 3 * 60 + 4,
        "idempotency_key": f"gh-hold:{lease.id}",
    }
    assert world.events == ["balance", "add", "create"]
    assert world.db.commits == 2
    assert _jobs(world.db.added) == []


def test_hold_create_client_error_releases_without_a_refund_job(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance = 500
    world.create_error = _http_error(402, "payment required")
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 409
    assert result.body == {"error": "funder_insufficient"}
    lease = _lease_row(world)
    assert lease.status == "released"
    assert lease.release_reason == "hold_failed"
    assert lease.hold_escrow_id is None
    assert _jobs(world.db.added) == []
    assert world.db.commits == 2


def test_hold_create_server_error_is_exchange_unavailable(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance = 500
    world.create_error = ConnectionError("reset")
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 502
    assert result.body == {"error": "exchange_unavailable"}
    lease = _lease_row(world)
    assert lease.status == "released"
    assert lease.release_reason == "hold_failed"
    # The escrow may exist; the refund job recovers it by replaying the create.
    assert github_leases.is_pending_hold(lease.hold_escrow_id)
    assert [job.payload for job in _jobs(world.db.added)] == [{"lease_id": str(lease.id)}]


def test_hold_create_409_duplicate_recovers_the_existing_escrow(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance = 500
    world.create_error = _http_error(
        409, "An active escrow already exists for this task_id (escrow_id=abcd-1234)"
    )
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 201
    assert _lease_row(world).hold_escrow_id == "abcd-1234"


def test_hold_create_without_an_id_releases_the_lease(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance = 500
    world.create_result = {}
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 502
    assert result.body == {"error": "exchange_unavailable"}
    assert _lease_row(world).release_reason == "hold_failed"


def test_losing_claim_does_not_create_an_escrow(monkeypatch):
    world = _Claim()
    world.lock = True
    world.balance = 500
    world.install(monkeypatch)
    world.db.commit_error = IntegrityError("INSERT", {}, Exception("duplicate key"))
    result = world.claim()
    assert result.status_code == 409
    assert result.body == {"error": "lease_held"}
    assert "create" not in world.events
    assert world.db.rollbacks == 1


def test_submit_keeps_the_lower_trust_values(monkeypatch):
    lease_id = uuid.uuid4()
    binding = types.SimpleNamespace(
        id=uuid.uuid4(),
        repo_full_name=REPO,
        issue_number=12,
        pr_number=None,
        lease_holder_agent_id=None,
        evaluated_head_sha="old",
        evaluated_spec_id=uuid.uuid4(),
        last_assessment={"outcome": "ACCEPTABLE"},
        review_cleared_by="alice",
    )
    lease = types.SimpleNamespace(
        id=lease_id,
        binding_id=binding.id,
        agent_id=AGENT,
        status="work",
        token_hash=github_leases.hash_lease_token(TOKEN),
        github_user_id=4242,
        created_at=FIXED,
        work_expires_at=FIXED + timedelta(minutes=5),
        hard_expires_at=FIXED + timedelta(hours=1),
        claim_kya=1,
        claim_reputation=0.1,
        pr_number=None,
        otel_trace_id=None,
        hold_expires_at=None,
    )
    link = types.SimpleNamespace(installation_id=9, repo_full_name=REPO)
    db = _DB()

    async def fetch_lease(db_session, wanted):
        return lease if wanted == lease_id else None

    async def fetch_binding(db_session, binding_id):
        return binding if binding_id == binding.id else None

    async def fetch_repo_link(db_session, repo):
        return link

    async def fetch_other_pr_binding(db_session, repo, pr_number, binding_id):
        return None

    async def lock_lease(db_session, wanted):
        return lease if wanted == lease_id else None

    async def get_pull_request(installation_id, repo, pr_number):
        return {
            "state": "open",
            "user": {"id": 4242},
            "body": "Fixes #12",
            "created_at": "2026-06-01T12:00:00Z",
            "head": {"sha": "abc"},
        }

    monkeypatch.setattr(github_leases, "fetch_lease", fetch_lease)
    monkeypatch.setattr(github_leases, "fetch_binding", fetch_binding)
    monkeypatch.setattr(github_leases, "fetch_repo_link", fetch_repo_link)
    monkeypatch.setattr(github_leases, "fetch_other_pr_binding", fetch_other_pr_binding)
    monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
    monkeypatch.setattr(github_leases, "_now", lambda: FIXED)
    monkeypatch.setattr(github_app, "get_pull_request", get_pull_request)
    claims = types.SimpleNamespace(agent_id=AGENT, kya_level=4, reputation=0.55)
    result = _run(
        github_leases.submit_lease(
            db,
            lease_id,
            claims,
            TOKEN,
            {"pr_url": f"https://github.com/{REPO}/pull/7"},
        )
    )
    assert result.status_code == 200
    assert lease.status == "submitted"
    assert lease.claim_kya == 1
    assert lease.claim_reputation == 0.1


def _holder(lease):
    return types.SimpleNamespace(agent_id=lease.agent_id)


def test_release_enqueues_a_refund_only_when_a_hold_exists(monkeypatch):
    async def release(lease):
        db = _DB()

        async def fetch_lease(db_session, lease_id):
            return lease if lease_id == lease.id else None

        async def lock_lease(db_session, lease_id):
            return lease if lease_id == lease.id else None

        monkeypatch.setattr(github_leases, "fetch_lease", fetch_lease)
        monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
        result = await github_leases.release_lease(db, lease.id, _holder(lease), TOKEN)
        return result, db

    held = types.SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=AGENT,
        status="work",
        token_hash=github_leases.hash_lease_token(TOKEN),
        hold_escrow_id="hold-1",
        release_reason=None,
    )
    result, db = _run(release(held))
    assert result.status_code == 200
    assert held.status == "released"
    assert held.release_reason == "agent_released"
    assert db.commits == 1
    jobs = _jobs(db.added)
    assert len(jobs) == 1
    assert jobs[0].kind == JobKind.REFUND_HOLD.value
    assert jobs[0].status == JobStatus.PENDING.value
    assert jobs[0].payload == {"lease_id": str(held.id)}

    plain = types.SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=AGENT,
        status="work",
        token_hash=github_leases.hash_lease_token(TOKEN),
        hold_escrow_id=None,
        release_reason=None,
    )
    plain_result, plain_db = _run(release(plain))
    assert plain_result.status_code == 200
    assert plain_db.added == []
    assert plain_db.commits == 1


def test_preempt_enqueues_a_refund_for_held_work_leases(monkeypatch):
    held = types.SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=AGENT,
        status="work",
        work_expires_at=FIXED + timedelta(minutes=5),
        hard_expires_at=FIXED + timedelta(hours=1),
        hold_escrow_id="hold-preempt",
        release_reason=None,
    )
    plain = types.SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=AGENT,
        status="work",
        work_expires_at=FIXED + timedelta(minutes=5),
        hard_expires_at=FIXED + timedelta(hours=1),
        hold_escrow_id=None,
        release_reason=None,
    )
    rows = {held.id: held, plain.id: plain}
    db = _DB()

    async def fetch_agent_work_leases(db_session, agent_id):
        return [held, plain]

    async def lock_lease(db_session, lease_id):
        return rows[lease_id]

    class _Redis:
        async def set(self, key, value, ex=None):
            return None

        async def aclose(self):
            return None

    monkeypatch.setattr(github_leases, "fetch_agent_work_leases", fetch_agent_work_leases)
    monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
    monkeypatch.setattr(github_leases, "_redis", lambda: _Redis())
    monkeypatch.setattr(github_leases, "_now", lambda: FIXED)
    result = _run(
        github_leases.heartbeat(
            db, types.SimpleNamespace(agent_id=AGENT), {"status": "BUSY_PREEMPTED"}
        )
    )
    assert result.status_code == 200
    assert held.status == "released"
    assert held.release_reason == "preempted"
    assert plain.status == "released"
    jobs = _jobs(db.added)
    assert len(jobs) == 1
    assert jobs[0].payload == {"lease_id": str(held.id)}
    assert db.commits == 1


def test_expiry_enqueues_a_refund_and_settles_an_adopted_hold(monkeypatch):
    now = FIXED
    monkeypatch.setattr(github_leases, "_now", lambda: now)
    expired = types.SimpleNamespace(
        id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        status="work",
        work_expires_at=now - timedelta(seconds=1),
        hard_expires_at=now + timedelta(hours=1),
        hold_expires_at=None,
        release_reason=None,
        hold_escrow_id="hold-expire",
    )
    plain = types.SimpleNamespace(
        id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        status="work",
        work_expires_at=now - timedelta(seconds=1),
        hard_expires_at=now + timedelta(hours=1),
        hold_expires_at=None,
        release_reason=None,
        hold_escrow_id=None,
    )
    kept = types.SimpleNamespace(
        id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        status="submitted",
        work_expires_at=now - timedelta(hours=2),
        hard_expires_at=now - timedelta(hours=1),
        hold_expires_at=now - timedelta(seconds=1),
        release_reason=None,
        hold_escrow_id="hold-keep",
        pr_number=8,
    )
    binding = types.SimpleNamespace(
        id=kept.binding_id,
        state="settlement_pending",
        pr_number=8,
        lease_holder_agent_id=AGENT,
        evaluated_head_sha="abc",
        evaluated_spec_id=uuid.uuid4(),
        last_assessment=None,
        review_cleared_by=None,
    )
    settlement = types.SimpleNamespace(
        binding_id=binding.id, spec_id=uuid.uuid4(), solver_agent_id=AGENT, escrow_id="hold-keep"
    )

    async def fetch_settlement(db, binding_id):
        return settlement if binding_id == binding.id else None

    monkeypatch.setattr(github_leases, "fetch_settlement", fetch_settlement)
    db = _Sweep([expired, plain, kept], {binding.id: binding})
    assert _run(github_leases.expire_stale_leases(db)) == 3
    assert db.committed is True
    assert expired.status == "expired"
    assert expired.release_reason == "work_window_elapsed"
    assert plain.status == "expired"
    assert kept.status == "settled"
    assert binding.pr_number == 8
    jobs = _jobs(db.added)
    assert [job.payload["lease_id"] for job in jobs] == [str(expired.id)]
    assert jobs[0].kind == JobKind.REFUND_HOLD.value


def _refund_lease(**overrides):
    data = {
        "id": uuid.uuid4(),
        "binding_id": uuid.uuid4(),
        "status": "released",
        "hold_escrow_id": "hold-1",
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def _install_refund(monkeypatch, lease, *, settlement=None, get_escrow=None, refund=None):
    funder_id = uuid.uuid4()
    funder = types.SimpleNamespace(
        id=funder_id, exchange_bot_id=FUNDER_AGENT, exchange_api_key="set"
    )
    binding_id = lease.binding_id if lease is not None else uuid.uuid4()
    bounty_id = uuid.uuid4()
    binding = types.SimpleNamespace(id=binding_id, repo_full_name=REPO, bounty_id=bounty_id)
    link = types.SimpleNamespace(funder_user_id=funder_id, repo_full_name=REPO)
    db = _DB({funder_id: funder})

    async def fetch_bounty_funder(db_session, wanted):
        return funder if wanted == bounty_id else None

    async def fetch_lease(db_session, lease_id):
        if lease is None or lease_id != lease.id:
            return None
        return lease

    async def fetch_settlement(db_session, binding_id):
        return settlement

    async def fetch_binding(db_session, wanted):
        return binding if lease is not None and wanted == lease.binding_id else None

    async def fetch_repo_link(db_session, repo):
        return link

    monkeypatch.setattr(github_leases, "fetch_lease", fetch_lease)
    monkeypatch.setattr(github_leases, "fetch_settlement", fetch_settlement)
    monkeypatch.setattr(github_leases, "fetch_binding", fetch_binding)
    monkeypatch.setattr(github_leases, "fetch_repo_link", fetch_repo_link)
    monkeypatch.setattr(github_leases, "fetch_bounty_funder", fetch_bounty_funder)
    if get_escrow is not None:
        monkeypatch.setattr(github_leases.exchange, "get_escrow", get_escrow)
    if refund is not None:
        monkeypatch.setattr(github_leases.exchange, "refund_escrow", refund)
    return db, funder


def test_refund_skips_missing_settled_adopted_and_non_held(monkeypatch):
    def forbid(*_args, **_kwargs):
        raise AssertionError("exchange should not be called")

    missing_db, _funder = _install_refund(monkeypatch, None, get_escrow=forbid, refund=forbid)
    _run(
        github_leases.handle_refund_hold(
            missing_db, types.SimpleNamespace(payload={"lease_id": str(uuid.uuid4())})
        )
    )

    bare = _refund_lease(hold_escrow_id=None)
    bare_db, _funder = _install_refund(monkeypatch, bare, get_escrow=forbid, refund=forbid)
    _run(
        github_leases.handle_refund_hold(
            bare_db, types.SimpleNamespace(payload={"lease_id": str(bare.id)})
        )
    )

    settled = _refund_lease(status="settled")

    async def boom_settlement(db_session, binding_id):
        raise AssertionError("settled lease should not load a settlement")

    settled_db, _funder = _install_refund(monkeypatch, settled, get_escrow=forbid, refund=forbid)
    monkeypatch.setattr(github_leases, "fetch_settlement", boom_settlement)
    _run(
        github_leases.handle_refund_hold(
            settled_db, types.SimpleNamespace(payload={"lease_id": str(settled.id)})
        )
    )

    adopted_lease = _refund_lease()
    adopted = types.SimpleNamespace(binding_id=adopted_lease.binding_id, escrow_id="hold-1")
    adopted_db, _funder = _install_refund(
        monkeypatch, adopted_lease, settlement=adopted, get_escrow=forbid, refund=forbid
    )
    _run(
        github_leases.handle_refund_hold(
            adopted_db, types.SimpleNamespace(payload={"lease_id": str(adopted_lease.id)})
        )
    )

    seen = {}

    def get_final(user, escrow_id):
        seen["status_read"] = escrow_id
        return {"status": "expired"}

    def refund_forbid(*_args, **_kwargs):
        raise AssertionError("refund")

    final_lease = _refund_lease()
    final_db, _funder = _install_refund(
        monkeypatch, final_lease, get_escrow=get_final, refund=refund_forbid
    )
    _run(
        github_leases.handle_refund_hold(
            final_db, types.SimpleNamespace(payload={"lease_id": str(final_lease.id)})
        )
    )
    assert seen["status_read"] == "hold-1"


@pytest.mark.parametrize("status", ["refunded", "released", "disputed"])
def test_refund_skips_terminal_escrow_statuses(monkeypatch, status):
    def get_escrow(user, escrow_id):
        return {"status": status}

    def refund(*_args, **_kwargs):
        raise AssertionError("refund")

    lease = _refund_lease()
    db, _funder = _install_refund(monkeypatch, lease, get_escrow=get_escrow, refund=refund)
    _run(
        github_leases.handle_refund_hold(
            db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
        )
    )


def test_refund_returns_a_held_escrow_to_the_funder(monkeypatch):
    calls = {}

    def get_escrow(user, escrow_id):
        calls["get"] = (user, escrow_id)
        return {"status": "held", "provider_id": AGENT, "amount": 100}

    def refund(user, escrow_id, reason=""):
        calls["refund"] = (user, escrow_id, reason)
        return {"status": "refunded"}

    lease = _refund_lease()
    other = types.SimpleNamespace(binding_id=lease.binding_id, escrow_id="some-other-escrow")
    db, funder = _install_refund(
        monkeypatch, lease, settlement=other, get_escrow=get_escrow, refund=refund
    )
    _run(
        github_leases.handle_refund_hold(
            db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
        )
    )
    assert calls["get"] == (funder, "hold-1")
    assert calls["refund"] == (funder, "hold-1", "lease ended")


def test_refund_treats_an_already_final_client_error_as_done(monkeypatch):
    def get_escrow(user, escrow_id):
        return {"status": "held"}

    def refund(user, escrow_id, reason=""):
        raise _http_error(400, "Escrow is already refunded")

    lease = _refund_lease()
    db, _funder = _install_refund(monkeypatch, lease, get_escrow=get_escrow, refund=refund)
    _run(
        github_leases.handle_refund_hold(
            db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
        )
    )


def test_refund_retries_transient_exchange_errors(monkeypatch):
    def get_down(user, escrow_id):
        raise _http_error(503, "unavailable")

    lease = _refund_lease()
    db, _funder = _install_refund(monkeypatch, lease, get_escrow=get_down)
    with pytest.raises(RetryableJobError, match="exchange unavailable"):
        _run(
            github_leases.handle_refund_hold(
                db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
            )
        )

    def get_held(user, escrow_id):
        return {"status": "held"}

    def refund_down(user, escrow_id, reason=""):
        raise ConnectionError("reset")

    retry_db, _funder = _install_refund(monkeypatch, lease, get_escrow=get_held, refund=refund_down)
    with pytest.raises(RetryableJobError, match="exchange unavailable"):
        _run(
            github_leases.handle_refund_hold(
                retry_db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
            )
        )


def test_refund_ignores_a_missing_escrow(monkeypatch):
    def get_missing(user, escrow_id):
        raise _http_error(404, "Escrow not found")

    def refund(*_args, **_kwargs):
        raise AssertionError("refund")

    lease = _refund_lease()
    db, _funder = _install_refund(monkeypatch, lease, get_escrow=get_missing, refund=refund)
    _run(
        github_leases.handle_refund_hold(
            db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
        )
    )


def _settlement_ctx(hold_id):
    binding_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    settlement = types.SimpleNamespace(
        id=uuid.uuid4(),
        binding_id=binding_id,
        spec_id=spec_id,
        solver_agent_id=AGENT,
        state="pending",
        escrow_id=None,
        idempotency_prefix="gh-prefix",
        last_error="stale",
        attempts=0,
    )
    funder = types.SimpleNamespace(
        id=uuid.uuid4(), exchange_bot_id=FUNDER_AGENT, exchange_api_key="set"
    )
    provider = types.SimpleNamespace(id=uuid.uuid4(), exchange_bot_id=AGENT, exchange_api_key="set")
    lease = types.SimpleNamespace(
        id=uuid.uuid4(),
        binding_id=binding_id,
        status="settled",
        hold_escrow_id=hold_id,
        agent_id=AGENT,
    )
    ctx = SettlementContext(
        settlement=settlement,
        binding=types.SimpleNamespace(id=binding_id, repo_full_name=REPO),
        spec=types.SimpleNamespace(id=spec_id, amount_ate=100, content_hash="spec"),
        bounty=types.SimpleNamespace(id=uuid.uuid4()),
        funder=funder,
        repo_link=None,
        ledger=None,
        provider=provider,
        lease=lease,
    )
    return ctx


def _commit_db(settlement):
    seen = []

    async def commit():
        seen.append(settlement.state)

    return types.SimpleNamespace(commit=commit), seen


@pytest.mark.parametrize(
    ("escrow", "adopt"),
    [
        ({"status": "held", "provider_id": AGENT, "amount": 100}, True),
        ({"status": "Held"}, True),
        ({"status": "held", "provider_id": AGENT}, True),
        ({"status": "held", "amount": "100"}, True),
        ({"status": "held", "provider_id": "other", "amount": 100}, False),
        ({"status": "held", "provider_id": AGENT, "amount": 99}, False),
        ({"status": "expired", "provider_id": AGENT, "amount": 100}, False),
    ],
)
def test_settlement_adopts_a_held_escrow_and_falls_back_otherwise(monkeypatch, escrow, adopt):
    ctx = _settlement_ctx("hold-1")
    db, commits = _commit_db(ctx.settlement)
    created = []

    def get_escrow(user, escrow_id):
        assert user is ctx.funder
        assert escrow_id == "hold-1"
        return escrow

    def create_escrow(user, **kwargs):
        created.append(kwargs)
        return {"escrow_id": "esc-new"}

    refunded = []
    monkeypatch.setattr(github_settlement.exchange, "get_escrow", get_escrow)
    monkeypatch.setattr(github_settlement.exchange, "create_escrow", create_escrow)
    monkeypatch.setattr(
        github_settlement.exchange,
        "refund_escrow",
        lambda user, escrow_id, reason="": refunded.append(escrow_id),
    )
    monkeypatch.setattr(settings, "ESCROW_TTL_MINUTES", 90)
    _run(github_settlement._step_create(db, ctx))
    held = str(escrow["status"]).lower() == "held"
    assert refunded == (["hold-1"] if held and not adopt else [])
    if adopt:
        assert created == []
        assert ctx.settlement.escrow_id == "hold-1"
        assert ctx.settlement.state == "escrow_created"
        assert ctx.settlement.last_error is None
        assert commits == ["escrow_created"]
    else:
        assert created == [
            {
                "provider_id": AGENT,
                "amount": 100,
                "task_id": f"gh:{ctx.settlement.binding_id}:{ctx.settlement.spec_id}",
                "ttl_minutes": 90,
                "idempotency_key": "gh-prefix:create",
            }
        ]
        assert ctx.settlement.escrow_id == "esc-new"
        assert ctx.settlement.state == "escrow_created"


def test_settlement_without_a_hold_creates_an_escrow(monkeypatch):
    ctx = _settlement_ctx(None)
    db, _commits = _commit_db(ctx.settlement)

    def get_escrow(*_args, **_kwargs):
        raise AssertionError("get_escrow")

    def create_escrow(user, **kwargs):
        return {"escrow_id": "esc-fresh"}

    monkeypatch.setattr(github_settlement.exchange, "get_escrow", get_escrow)
    monkeypatch.setattr(github_settlement.exchange, "create_escrow", create_escrow)
    _run(github_settlement._step_create(db, ctx))
    assert ctx.settlement.escrow_id == "esc-fresh"
    assert ctx.settlement.state == "escrow_created"


def test_settlement_adopts_when_the_hold_is_gone_by_creating(monkeypatch):
    ctx = _settlement_ctx("hold-1")
    db, _commits = _commit_db(ctx.settlement)

    def get_escrow(user, escrow_id):
        raise _http_error(404, "Escrow not found")

    def create_escrow(user, **kwargs):
        return {"escrow_id": "esc-replacement"}

    monkeypatch.setattr(github_settlement.exchange, "get_escrow", get_escrow)
    monkeypatch.setattr(github_settlement.exchange, "create_escrow", create_escrow)
    _run(github_settlement._step_create(db, ctx))
    assert ctx.settlement.escrow_id == "esc-replacement"


def test_settlement_does_not_create_a_second_escrow_when_the_hold_lookup_fails(monkeypatch):
    ctx = _settlement_ctx("hold-1")
    db, _commits = _commit_db(ctx.settlement)

    def get_escrow(user, escrow_id):
        raise _http_error(503, "unavailable")

    def create_escrow(user, **kwargs):
        raise AssertionError("create_escrow")

    monkeypatch.setattr(github_settlement.exchange, "get_escrow", get_escrow)
    monkeypatch.setattr(github_settlement.exchange, "create_escrow", create_escrow)
    with pytest.raises(httpx.HTTPStatusError):
        _run(github_settlement._step_create(db, ctx))
    assert ctx.settlement.escrow_id is None
    assert ctx.settlement.state == "pending"


def test_settlement_does_not_create_when_the_stale_hold_refund_fails(monkeypatch):
    ctx = _settlement_ctx("hold-1")
    db, _commits = _commit_db(ctx.settlement)

    def refund(user, escrow_id, reason=""):
        raise ConnectionError("reset")

    def create_escrow(user, **kwargs):
        raise AssertionError("create_escrow")

    monkeypatch.setattr(
        github_settlement.exchange,
        "get_escrow",
        lambda user, escrow_id: {"status": "held", "provider_id": "other"},
    )
    monkeypatch.setattr(github_settlement.exchange, "refund_escrow", refund)
    monkeypatch.setattr(github_settlement.exchange, "create_escrow", create_escrow)
    with pytest.raises(ConnectionError):
        _run(github_settlement._step_create(db, ctx))
    assert ctx.settlement.escrow_id is None


def test_settlement_resolves_a_pending_hold_by_replaying_the_create(monkeypatch):
    ctx = _settlement_ctx(github_leases.HOLD_PENDING)
    db, _commits = _commit_db(ctx.settlement)
    replays = []

    def create_escrow(user, **kwargs):
        replays.append(kwargs["idempotency_key"])
        return {"escrow_id": "hold-recovered"}

    monkeypatch.setattr(github_leases.exchange, "create_escrow", create_escrow)
    monkeypatch.setattr(
        github_settlement.exchange,
        "get_escrow",
        lambda user, escrow_id: {"status": "held", "provider_id": AGENT, "amount": 100},
    )
    _run(github_settlement._step_create(db, ctx))
    assert replays == [f"gh-hold:{ctx.lease.id}"]
    assert ctx.settlement.escrow_id == "hold-recovered"
    assert ctx.lease.hold_escrow_id == "hold-recovered"


def test_refund_resolves_a_pending_hold_then_refunds_it(monkeypatch):
    calls = {}
    lease = _refund_lease(
        hold_escrow_id=github_leases.HOLD_PENDING, agent_id=AGENT, spec_id=uuid.uuid4()
    )

    def refund(user, escrow_id, reason=""):
        calls["refund"] = escrow_id

    db, _funder = _install_refund(
        monkeypatch,
        lease,
        get_escrow=lambda user, escrow_id: {"status": "held"},
        refund=refund,
    )

    async def fetch_spec(db_session, spec_id):
        return types.SimpleNamespace(amount_ate=100)

    monkeypatch.setattr(github_leases, "fetch_spec", fetch_spec)
    monkeypatch.setattr(
        github_leases.exchange, "create_escrow", lambda user, **kw: {"escrow_id": "hold-late"}
    )
    _run(
        github_leases.handle_refund_hold(
            db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
        )
    )
    assert lease.hold_escrow_id == "hold-late"
    assert calls["refund"] == "hold-late"


def test_refund_of_a_pending_hold_the_exchange_refuses_is_done(monkeypatch):
    lease = _refund_lease(
        hold_escrow_id=github_leases.HOLD_PENDING, agent_id=AGENT, spec_id=uuid.uuid4()
    )

    def forbid(*_args, **_kwargs):
        raise AssertionError("no escrow to read or refund")

    db, _funder = _install_refund(monkeypatch, lease, get_escrow=forbid, refund=forbid)

    async def fetch_spec(db_session, spec_id):
        return types.SimpleNamespace(amount_ate=100)

    def create_escrow(user, **kwargs):
        raise _http_error(402, "insufficient balance")

    monkeypatch.setattr(github_leases, "fetch_spec", fetch_spec)
    monkeypatch.setattr(github_leases.exchange, "create_escrow", create_escrow)
    _run(
        github_leases.handle_refund_hold(
            db, types.SimpleNamespace(payload={"lease_id": str(lease.id)})
        )
    )
    assert lease.hold_escrow_id is None


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"links": [{"principal_id": "p-1", "confidence": 0.9}]}, {"p-1": 0.9}),
        (
            200,
            {"links": [{"principal_id": "p-1", "confidence": 0.9}, {"principal_id": "p-2"}]},
            {"p-1": 0.9, "p-2": 1.0},
        ),
        (200, {"links": []}, {}),
        (404, {"detail": "not found"}, {}),
        (503, {"detail": "down"}, "raise"),
        (200, {"links": [{}]}, "raise"),
    ],
)
def test_principal_lookup_separates_no_principal_from_an_outage(status, body, expected):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))

    async def lookup():
        async with httpx.AsyncClient(transport=transport) as client:
            return await github_leases._principal_links(client, "agent-1", "key-1")

    if expected == "raise":
        with pytest.raises(github_leases._PrincipalUnavailable):
            _run(lookup())
    else:
        assert _run(lookup()) == expected


@pytest.mark.parametrize(
    ("funder_links", "solver_links", "refused"),
    [
        ({"p-main": 0.9, "p-shared": 0.8}, {"p-solver": 0.9, "p-shared": 0.6}, True),
        ({"p-main": 0.9, "p-shared": 0.3}, {"p-shared": 0.9}, False),
        ({"p-main": 0.9}, {"p-solver": 0.9}, False),
    ],
)
def test_self_dealing_compares_every_principal_link(
    monkeypatch, funder_links, solver_links, refused
):
    world = _Claim()
    world.principals = {FUNDER_AGENT: funder_links, AGENT: solver_links}
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == (403 if refused else 201)


def test_replayed_hold_keeps_the_original_ttl_after_a_config_change(monkeypatch):
    seen = []

    def create_escrow(user, **kwargs):
        seen.append(kwargs["ttl_minutes"])
        return {"escrow_id": "hold-1"}

    monkeypatch.setattr(github_leases.exchange, "create_escrow", create_escrow)
    monkeypatch.setattr(settings, "GITHUB_HOLD_TTL_MARGIN_MINUTES", 999)
    lease = types.SimpleNamespace(
        id=uuid.uuid4(), agent_id=AGENT, hold_escrow_id=github_leases.pending_hold_marker(4321)
    )
    assert _run(github_leases.resolve_hold(object(), lease, 100)) == "hold-1"
    assert seen == [4321]


def test_an_unrecovered_409_is_not_proof_that_no_hold_exists(monkeypatch):
    def create_escrow(user, **kwargs):
        raise _http_error(409, "Idempotency key reused with a different request body")

    monkeypatch.setattr(github_leases.exchange, "create_escrow", create_escrow)
    lease = types.SimpleNamespace(
        id=uuid.uuid4(), agent_id=AGENT, hold_escrow_id=github_leases.pending_hold_marker(60)
    )
    with pytest.raises(httpx.HTTPStatusError):
        _run(github_leases.resolve_hold(object(), lease, 100))


def test_principal_lookups_use_each_accounts_own_key(monkeypatch):
    world = _Claim()
    world.install(monkeypatch)
    monkeypatch.setattr(settings, "GATEWAY_EXCHANGE_API_KEY", "gateway-key")
    assert world.claim().status_code == 201
    assert world.principal_calls == [FUNDER_AGENT, AGENT]
    assert world.principal_keys == ["funder-key", "solver-key"]


def test_principal_lookup_falls_back_to_the_gateway_key(monkeypatch):
    world = _Claim()
    world.solver.exchange_api_key = None
    world.funder.exchange_api_key = None
    world.install(monkeypatch)
    monkeypatch.setattr(settings, "GATEWAY_EXCHANGE_API_KEY", "gateway-key")
    assert world.claim().status_code == 201
    assert world.principal_keys == ["gateway-key", "gateway-key"]


def test_principal_lookup_sends_the_key_as_a_bearer_token():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"links": []})

    async def lookup():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await github_leases._principal_links(client, "agent-1", "own-key")

    assert _run(lookup()) == {}
    assert seen == ["Bearer own-key"]
