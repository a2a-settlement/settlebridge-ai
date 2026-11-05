"""Agent lease HTTP contract. No database and no network."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import get_db
from app.models.github import GitHubJob, JobKind
from app.services import github_app, github_leases, trust_gateway
from app.services.trust_gateway import AgentClaims, TrustTokenError
from app.routes.github_leases import router

AGENT = "bot-1"
REPO = "acme/widget"
ISSUE = 12
TOKEN = "sbl_testtoken"
FIXED = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
SPEC_KEYS = {
    "spec_id",
    "version",
    "content_hash",
    "test_command",
    "required_checks",
    "policy",
    "payment_trigger",
    "required_kya",
    "required_ema",
}


def _parse(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _binding(**overrides):
    data = {
        "id": uuid.uuid4(),
        "bounty_id": uuid.uuid4(),
        "repo_full_name": REPO,
        "issue_number": ISSUE,
        "current_spec_id": uuid.uuid4(),
        "state": "advertised",
        "pr_number": None,
        "lease_holder_agent_id": None,
        "evaluated_head_sha": "sha-old",
        "evaluated_spec_id": uuid.uuid4(),
        "last_assessment": {"outcome": "ACCEPTABLE", "findings": [{"msg": "old"}]},
        "review_cleared_by": "alice",
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def _spec(binding, **overrides):
    data = {
        "id": binding.current_spec_id,
        "version": 3,
        "content_hash": "ab" * 32,
        "test_command": "pytest -q",
        "required_checks": [{"name": "ci", "app_id": 1}],
        "policy": {"deny_paths": ["secrets/"]},
        "payment_trigger": "merge_with_checks",
        "required_kya": 2,
        "required_ema": 0.8,
        "amount_ate": 2500,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def _user():
    return types.SimpleNamespace(id=uuid.uuid4())


def _identity(user, github_user_id=4242):
    return types.SimpleNamespace(user_id=user.id, github_user_id=github_user_id, login="octocat")


def _lease(binding, user, **overrides):
    data = {
        "id": uuid.uuid4(),
        "binding_id": binding.id,
        "agent_id": AGENT,
        "user_id": user.id,
        "spec_id": binding.current_spec_id,
        "token_hash": github_leases.hash_lease_token(TOKEN),
        "status": "work",
        "work_expires_at": FIXED + timedelta(minutes=10),
        "hard_expires_at": FIXED + timedelta(hours=2),
        "hold_expires_at": None,
        "pr_number": None,
        "otel_trace_id": None,
        "release_reason": None,
        "github_user_id": 4242,
        "created_at": FIXED,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.commit_error = None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        self.rollbacks += 1


def _headers(*, bearer="good-token", lease=TOKEN, auth=True, lease_header=True):
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {bearer}"
    if lease_header:
        headers["X-Lease-Token"] = lease
    return headers


def _error(response, status, error):
    assert response.status_code == status
    body = response.json()
    assert body["error"] == error
    assert "detail" not in body
    return body


@pytest.fixture
def api(monkeypatch):
    binding = _binding()
    spec = _spec(binding)
    user = _user()
    identity = _identity(user)
    lease = _lease(binding, user)
    bounty = types.SimpleNamespace(id=binding.bounty_id, title="Fix the widget", tags=["python"])
    box = {
        "binding": binding,
        "advertised": binding,
        "spec": spec,
        "users": [user],
        "identity": identity,
        "active_count": 0,
        "expiries": [],
        "active_lease": None,
        "lease": lease,
        "link": types.SimpleNamespace(installation_id=99, repo_full_name=REPO),
        "other_pr": None,
        "settlement": None,
        "status_binding": binding,
        "work_leases": [],
        "rows": [],
        "listing_calls": [],
        "pr_calls": [],
        "pr_error": None,
        "pull": {
            "state": "open",
            "number": 7,
            "user": {"id": 4242, "login": "octocat"},
            "body": "Fixes #12",
            "head": {"sha": "a" * 40},
            "created_at": "2026-06-01T12:00:00Z",
        },
        "redis_error": False,
        "redis_clients": [],
        "agent_id": AGENT,
        "denial": None,
        "bounty": bounty,
        "user": user,
        "scopes": frozenset({"mcp:tool:financial"}),
        "events": [],
        "lock_calls": [],
    }

    async def fetch_advertised_bounties(db, *, language, limit):
        box["listing_calls"].append((language, limit))
        return list(box["rows"])

    async def fetch_advertised_binding(db, bounty_id):
        advertised = box["advertised"]
        if advertised is None or advertised.bounty_id != bounty_id:
            return None
        return advertised

    async def fetch_spec(db, spec_id):
        spec_row = box["spec"]
        if spec_row is None or spec_row.id != spec_id:
            return None
        return spec_row

    async def fetch_users_for_agent(db, agent_id):
        return list(box["users"])

    async def fetch_identity_for_user(db, user_id):
        identity_row = box["identity"]
        if identity_row is None or identity_row.user_id != user_id:
            return None
        return identity_row

    async def count_active_leases(db, agent_id):
        box["events"].append(("count", agent_id))
        return box["active_count"]

    async def fetch_expired_timestamps(db, agent_id, since):
        box["events"].append(("expiries", agent_id))
        return list(box["expiries"])

    async def lock_agent_for_claim(db, agent_id):
        box["events"].append(("lock", agent_id))

    async def lock_lease(db, lease_id):
        box["lock_calls"].append(lease_id)
        current = box["lease"]
        if current is not None and getattr(current, "id", None) == lease_id:
            return current
        for item in box["work_leases"]:
            if getattr(item, "id", None) == lease_id:
                return item
        return None

    async def fetch_active_lease_for_binding(db, binding_id):
        active = box["active_lease"]
        if active is None:
            return None
        if getattr(active, "binding_id", binding_id) != binding_id:
            return None
        return active

    async def fetch_lease(db, lease_id):
        current = box["lease"]
        if current is None or current.id != lease_id:
            return None
        return current

    async def fetch_binding(db, binding_id):
        current = box["binding"]
        if current is None or current.id != binding_id:
            return None
        return current

    async def fetch_repo_link(db, repo_full_name):
        return box["link"]

    async def fetch_other_pr_binding(db, repo_full_name, pr_number, binding_id):
        return box["other_pr"]

    async def fetch_binding_by_bounty(db, bounty_id):
        current = box["status_binding"]
        if current is None or current.bounty_id != bounty_id:
            return None
        return current

    async def fetch_settlement(db, binding_id):
        settlement = box["settlement"]
        if settlement is None or settlement.binding_id != binding_id:
            return None
        return settlement

    async def fetch_agent_work_leases(db, agent_id):
        return list(box["work_leases"])

    async def get_pull_request(installation_id, repo, pr_number):
        box["pr_calls"].append((installation_id, repo, pr_number))
        if box["pr_error"] is not None:
            raise box["pr_error"]
        return box["pull"]

    class FakeRedis:
        def __init__(self):
            self.items = []
            self.closed = False

        async def set(self, key, value, ex=None):
            if box["redis_error"]:
                raise ConnectionError("redis down")
            self.items.append((key, value, ex))

        async def aclose(self):
            self.closed = True

    def redis_factory():
        client = FakeRedis()
        box["redis_clients"].append(client)
        return client

    def bearer(header):
        if not isinstance(header, str) or not header.startswith("Bearer "):
            raise TrustTokenError("missing")
        token = header[7:].strip()
        if not token:
            raise TrustTokenError("missing")
        return token

    def verify(token):
        if token == "bad":
            raise TrustTokenError("bad")
        return AgentClaims(
            agent_id=box["agent_id"],
            org_id="org-1",
            kya_level=2,
            reputation=0.9,
            scopes=box["scopes"],
            raw={},
        )

    def denial(claims, *, required_kya, required_ema):
        return box["denial"]

    monkeypatch.setattr(github_leases, "fetch_advertised_bounties", fetch_advertised_bounties)
    monkeypatch.setattr(github_leases, "fetch_advertised_binding", fetch_advertised_binding)
    monkeypatch.setattr(github_leases, "fetch_spec", fetch_spec)
    monkeypatch.setattr(github_leases, "fetch_users_for_agent", fetch_users_for_agent)
    monkeypatch.setattr(github_leases, "fetch_identity_for_user", fetch_identity_for_user)
    monkeypatch.setattr(github_leases, "count_active_leases", count_active_leases)
    monkeypatch.setattr(github_leases, "fetch_expired_timestamps", fetch_expired_timestamps)
    monkeypatch.setattr(github_leases, "lock_agent_for_claim", lock_agent_for_claim)
    monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
    monkeypatch.setattr(
        github_leases, "fetch_active_lease_for_binding", fetch_active_lease_for_binding
    )
    monkeypatch.setattr(github_leases, "fetch_lease", fetch_lease)
    monkeypatch.setattr(github_leases, "fetch_binding", fetch_binding)
    monkeypatch.setattr(github_leases, "fetch_repo_link", fetch_repo_link)
    monkeypatch.setattr(github_leases, "fetch_other_pr_binding", fetch_other_pr_binding)
    monkeypatch.setattr(github_leases, "fetch_binding_by_bounty", fetch_binding_by_bounty)
    monkeypatch.setattr(github_leases, "fetch_settlement", fetch_settlement)
    monkeypatch.setattr(github_leases, "fetch_agent_work_leases", fetch_agent_work_leases)
    monkeypatch.setattr(github_leases, "_redis", redis_factory)
    monkeypatch.setattr(github_leases, "_now", lambda: FIXED)
    monkeypatch.setattr(github_app, "get_pull_request", get_pull_request)
    monkeypatch.setattr(trust_gateway, "bearer_token", bearer, raising=False)
    monkeypatch.setattr(trust_gateway, "verify_gateway_token", verify)
    monkeypatch.setattr(trust_gateway, "trust_denial", denial)

    session = FakeSession()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield types.SimpleNamespace(
            client=client, session=session, box=box, monkeypatch=monkeypatch
        )
    finally:
        client.close()


def test_list_bounties_shape_and_limit(api, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_ATE_PER_USD", 100)
    binding = api.box["binding"]
    spec = api.box["spec"]
    listed = api.client.get("/api/v1/github/bounties")
    assert listed.status_code == 200
    assert listed.json() == {"bounties": []}
    assert api.box["listing_calls"] == [(None, 50)]

    api.box["rows"] = [(binding, api.box["bounty"], spec)]
    response = api.client.get("/api/v1/github/bounties?language=Python&limit=500")
    assert response.status_code == 200
    assert api.box["listing_calls"][-1] == ("Python", 200)
    item = response.json()["bounties"][0]
    assert set(item) == {
        "bounty_id",
        "repo",
        "issue_number",
        "issue_url",
        "title",
        "amount_ate",
        "amount_usd",
        "funding_status",
        "spec",
    }
    assert item["bounty_id"] == str(binding.bounty_id)
    assert item["repo"] == REPO
    assert item["issue_number"] == ISSUE
    assert item["issue_url"] == "https://github.com/acme/widget/issues/12"
    assert item["title"] == "Fix the widget"
    assert item["amount_ate"] == 2500
    assert item["amount_usd"] == "25"
    assert item["funding_status"] == "balance_checked_not_reserved"
    assert set(item["spec"]) == SPEC_KEYS
    assert item["spec"]["spec_id"] == str(spec.id)
    assert item["spec"]["version"] == 3
    assert item["spec"]["test_command"] == "pytest -q"
    assert item["spec"]["required_kya"] == 2
    assert item["spec"]["required_ema"] == 0.8
    assert "token" not in response.text


def test_claim_lease_happy_path(api, monkeypatch):
    monkeypatch.setattr(settings, "LEASE_WORK_MINUTES", 7)
    monkeypatch.setattr(settings, "LEASE_HARD_CAP_MINUTES", 40)
    binding = api.box["binding"]
    response = api.client.post(
        f"/api/v1/github/bounties/{binding.bounty_id}/claim-lease",
        headers=_headers(lease_header=False),
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
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
    assert body["lease_token"].startswith("sbl_")
    assert body["bounty_id"] == str(binding.bounty_id)
    assert body["repo"] == REPO
    assert body["issue_number"] == ISSUE
    assert body["issue_url"] == f"https://github.com/{REPO}/issues/{ISSUE}"
    assert body["test_command"] == "pytest -q"
    assert set(body["spec"]) == SPEC_KEYS
    assert _parse(body["work_expires_at"]) == FIXED + timedelta(minutes=7)
    assert _parse(body["hard_expires_at"]) == FIXED + timedelta(minutes=40)
    lease = api.session.added[0]
    assert str(lease.id) == body["lease_id"]
    assert lease.token_hash == github_leases.hash_lease_token(body["lease_token"])
    assert lease.token_hash != body["lease_token"]
    assert lease.status == "work"
    assert lease.agent_id == AGENT
    assert lease.user_id == api.box["user"].id
    assert lease.spec_id == binding.current_spec_id
    assert lease.binding_id == binding.id
    assert lease.github_user_id == 4242
    assert lease.created_at == FIXED
    assert api.session.commits == 1
    assert api.session.rollbacks == 0


def test_claim_requires_gateway_token(api):
    binding = api.box["binding"]
    path = f"/api/v1/github/bounties/{binding.bounty_id}/claim-lease"
    missing = api.client.post(path)
    _error(missing, 401, "invalid_token")
    wrong_scheme = api.client.post(path, headers={"Authorization": "Token abc"})
    _error(wrong_scheme, 401, "invalid_token")
    bad = api.client.post(path, headers=_headers(bearer="bad", lease_header=False))
    _error(bad, 401, "invalid_token")
    assert api.session.commits == 0


def test_claim_not_found_before_trust_denial(api):
    api.box["advertised"] = None
    api.box["denial"] = {"error": "trust_insufficient"}
    response = api.client.post(
        f"/api/v1/github/bounties/{uuid.uuid4()}/claim-lease",
        headers=_headers(lease_header=False),
    )
    _error(response, 404, "not_found")


def test_claim_missing_spec_is_not_found(api):
    api.box["spec"] = None
    response = api.client.post(
        f"/api/v1/github/bounties/{api.box['binding'].bounty_id}/claim-lease",
        headers=_headers(lease_header=False),
    )
    _error(response, 404, "not_found")


def test_claim_trust_denial_before_account_lookup(api):
    api.box["users"] = []
    api.box["denial"] = {"error": "trust_insufficient", "required_kya": 4}
    response = api.client.post(
        f"/api/v1/github/bounties/{api.box['binding'].bounty_id}/claim-lease",
        headers=_headers(lease_header=False),
    )
    assert response.status_code == 403
    assert response.json() == {"error": "trust_insufficient", "required_kya": 4}


@pytest.mark.parametrize(
    ("users", "identity", "count", "expiries", "held", "status", "error"),
    [
        ([], "keep", 0, [], None, 409, "unknown_agent_account"),
        ("two", "keep", 0, [], None, 409, "ambiguous_agent_account"),
        ("one", None, 99, [], None, 409, "no_github_identity"),
        ("one", "keep", 2, "cool", None, 409, "lease_limit"),
        ("one", "keep", 0, "cool", None, 429, "cooldown"),
        ("one", "keep", 0, [], "held", 409, "lease_held"),
    ],
)
def test_claim_rejects_account_cap_and_hold(
    api, users, identity, count, expiries, held, status, error
):
    if users == "two":
        api.box["users"] = [api.box["user"], types.SimpleNamespace(id=uuid.uuid4())]
    elif users == []:
        api.box["users"] = []
    if identity is None:
        api.box["identity"] = None
    api.box["active_count"] = count
    if expiries == "cool":
        api.box["expiries"] = [
            FIXED - timedelta(minutes=50),
            FIXED - timedelta(minutes=40),
            FIXED - timedelta(minutes=10),
        ]
    if held == "held":
        api.box["active_lease"] = types.SimpleNamespace()
    response = api.client.post(
        f"/api/v1/github/bounties/{api.box['binding'].bounty_id}/claim-lease",
        headers=_headers(lease_header=False),
    )
    body = _error(response, status, error)
    if error == "cooldown":
        assert body["retry_after_s"] == 600
    assert api.session.commits == 0


def test_claim_integrity_error_is_lease_held(api):
    api.session.commit_error = IntegrityError("INSERT", {}, Exception("duplicate key"))
    response = api.client.post(
        f"/api/v1/github/bounties/{api.box['binding'].bounty_id}/claim-lease",
        headers=_headers(lease_header=False),
    )
    _error(response, 409, "lease_held")
    assert api.session.rollbacks == 1
    assert "lease_token" not in response.text


def test_renew_extends_until_hard_cap(api, monkeypatch):
    monkeypatch.setattr(settings, "LEASE_WORK_MINUTES", 15)
    lease = api.box["lease"]
    response = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"lease_id", "work_expires_at", "hard_expires_at"}
    assert body["lease_id"] == str(lease.id)
    assert _parse(body["work_expires_at"]) == FIXED + timedelta(minutes=15)
    assert lease.work_expires_at == FIXED + timedelta(minutes=15)
    assert api.session.commits == 1

    lease.work_expires_at = FIXED + timedelta(minutes=1)
    lease.hard_expires_at = FIXED + timedelta(minutes=4)
    capped = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    assert capped.status_code == 200
    assert _parse(capped.json()["work_expires_at"]) == lease.hard_expires_at
    assert lease.work_expires_at == FIXED + timedelta(minutes=4)


def test_renew_rejects_inactive_and_foreign_holder(api):
    lease = api.box["lease"]
    missing = api.client.post(f"/api/v1/github/leases/{uuid.uuid4()}/renew", headers=_headers())
    _error(missing, 404, "not_found")

    wrong = api.client.post(
        f"/api/v1/github/leases/{lease.id}/renew",
        headers=_headers(lease="sbl_other"),
    )
    _error(wrong, 403, "not_lease_holder")
    bare = api.client.post(
        f"/api/v1/github/leases/{lease.id}/renew",
        headers=_headers(lease_header=False),
    )
    _error(bare, 403, "not_lease_holder")
    api.box["agent_id"] = "someone-else"
    foreign = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    _error(foreign, 403, "not_lease_holder")
    api.box["agent_id"] = AGENT

    original = lease.work_expires_at
    lease.hard_expires_at = FIXED
    at_cap = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    _error(at_cap, 409, "lease_not_active")
    assert lease.work_expires_at == original

    lease.status = "submitted"
    submitted = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    _error(submitted, 409, "lease_not_active")
    assert api.session.commits == 0


def test_release_work_lease(api):
    lease = api.box["lease"]
    lease.work_expires_at = FIXED - timedelta(minutes=1)
    response = api.client.post(f"/api/v1/github/leases/{lease.id}/release", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"lease_id": str(lease.id), "status": "released"}
    assert lease.status == "released"
    assert lease.release_reason == "agent_released"
    assert api.session.commits == 1


def test_release_rejects_submitted_and_inactive(api):
    lease = api.box["lease"]
    lease.status = "submitted"
    submitted = api.client.post(f"/api/v1/github/leases/{lease.id}/release", headers=_headers())
    _error(submitted, 409, "lease_submitted")
    assert lease.status == "submitted"
    assert lease.release_reason is None

    lease.status = "expired"
    expired = api.client.post(f"/api/v1/github/leases/{lease.id}/release", headers=_headers())
    _error(expired, 409, "lease_not_active")
    assert api.session.commits == 0


def _submit(api, lease, payload):
    return api.client.post(
        f"/api/v1/github/leases/{lease.id}/submit",
        headers=_headers(),
        json=payload,
    )


def test_submit_binds_matching_open_pr(api, monkeypatch):
    monkeypatch.setattr(settings, "LEASE_SUBMITTED_HOLD_HOURS", 72)
    lease = api.box["lease"]
    binding = api.box["binding"]
    trace = "t" * 80
    response = _submit(
        api,
        lease,
        {"pr_url": "https://github.com/Acme/Widget/pull/7", "otel_trace_id": trace},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"lease_id", "status", "pr_number", "hold_expires_at"}
    assert body["lease_id"] == str(lease.id)
    assert body["status"] == "submitted"
    assert body["pr_number"] == 7
    assert _parse(body["hold_expires_at"]) == FIXED + timedelta(hours=72)
    assert api.box["pr_calls"] == [(99, REPO, 7)]
    assert lease.status == "submitted"
    assert lease.pr_number == 7
    assert lease.otel_trace_id == "t" * 64
    assert lease.hold_expires_at == FIXED + timedelta(hours=72)
    assert binding.pr_number == 7
    assert binding.lease_holder_agent_id == AGENT
    assert binding.evaluated_head_sha is None
    assert binding.evaluated_spec_id is None
    assert binding.last_assessment is None
    assert binding.review_cleared_by is None
    assert api.session.commits == 1
    jobs = [obj for obj in api.session.added if isinstance(obj, GitHubJob)]
    assert len(jobs) == 1
    assert jobs[0].kind == JobKind.CHECKS_COMPLETED.value
    data = jobs[0].payload["data"]
    assert data["check_suite"] == {"head_sha": "a" * 40, "pull_requests": [{"number": 7}]}
    assert data["repository"] == {"full_name": REPO}
    assert data["installation"] == {"id": 99}


def test_submit_accepts_null_trace_at_window_boundary(api):
    lease = api.box["lease"]
    lease.work_expires_at = FIXED
    lease.hard_expires_at = FIXED
    response = _submit(
        api,
        lease,
        {"pr_url": f"https://github.com/{REPO}/pull/7", "otel_trace_id": None},
    )
    assert response.status_code == 200
    assert lease.otel_trace_id is None
    assert lease.status == "submitted"


def test_submit_wrong_repo_does_not_call_github(api):
    lease = api.box["lease"]
    response = _submit(api, lease, {"pr_url": "http://github.com/acme/widget/pull/7"})
    _error(response, 422, "pr_wrong_repo")
    other = _submit(api, lease, {"pr_url": "https://github.com/other/repo/pull/7"})
    _error(other, 422, "pr_wrong_repo")
    assert api.box["pr_calls"] == []
    assert lease.status == "work"
    assert api.session.commits == 0


def test_submit_rejects_closed_unlinked_or_foreign_pr(api):
    lease = api.box["lease"]
    binding = api.box["binding"]
    api.box["pull"] = {**api.box["pull"], "state": "closed"}
    closed = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(closed, 422, "pr_not_open")

    api.box["pull"] = {**api.box["pull"], "state": "open", "body": "See #12"}
    unlinked = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(unlinked, 422, "pr_not_referencing_issue")

    api.box["pull"] = {**api.box["pull"], "body": "Fixes #99"}
    other_issue = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(other_issue, 422, "pr_not_referencing_issue")

    api.box["pull"] = {**api.box["pull"], "body": "Fixes #12", "user": {"id": 7, "login": "eve"}}
    mismatch = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(mismatch, 422, "pr_author_mismatch")
    assert lease.status == "work"
    assert binding.pr_number is None
    assert binding.lease_holder_agent_id is None
    assert binding.evaluated_head_sha == "sha-old"
    assert api.session.commits == 0


def test_submit_rejects_pr_already_bound_and_github_outage(api):
    lease = api.box["lease"]
    api.box["other_pr"] = types.SimpleNamespace(id=uuid.uuid4())
    bound = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(bound, 409, "pr_already_bound")
    assert lease.status == "work"

    api.box["other_pr"] = None
    api.box["pr_error"] = github_app.GitHubAPIError("unavailable", 503)
    down = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(down, 502, "github_unavailable")

    api.box["pr_error"] = None
    api.box["link"] = None
    missing = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(missing, 502, "github_unavailable")
    assert api.session.commits == 0


def test_submit_inactive_lease_does_not_fetch_the_pr(api):
    lease = api.box["lease"]
    lease.work_expires_at = FIXED - timedelta(seconds=1)
    response = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(response, 409, "lease_not_active")
    lease.work_expires_at = FIXED + timedelta(minutes=5)
    lease.hard_expires_at = FIXED - timedelta(seconds=1)
    hard = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(hard, 409, "lease_not_active")
    lease.status = "released"
    released = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(released, 409, "lease_not_active")
    assert api.box["pr_calls"] == []


def test_bounty_status_public_shape(api):
    binding = api.box["binding"]
    lease = api.box["lease"]
    lease.status = "submitted"
    lease.hold_expires_at = FIXED + timedelta(hours=1)
    api.box["active_lease"] = lease
    api.box["settlement"] = types.SimpleNamespace(
        binding_id=binding.id, state="escrow_created", escrow_id="escrow-9"
    )
    response = api.client.get(f"/api/v1/github/bounties/{binding.bounty_id}/status")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "bounty_id",
        "state",
        "pr_number",
        "evaluated_head_sha",
        "assessment",
        "settlement",
        "lease",
    }
    assert body["bounty_id"] == str(binding.bounty_id)
    assert body["state"] == "advertised"
    assert body["pr_number"] is None
    assert body["evaluated_head_sha"] == "sha-old"
    assert body["assessment"] == {"outcome": "ACCEPTABLE", "findings": [{"msg": "old"}]}
    assert body["settlement"] == {"state": "escrow_created"}
    assert "escrow_id" not in response.text
    assert set(body["lease"]) == {"active", "status", "work_expires_at", "hold_expires_at"}
    assert body["lease"]["active"] is True
    assert body["lease"]["status"] == "submitted"
    assert _parse(body["lease"]["work_expires_at"]) == lease.work_expires_at
    assert _parse(body["lease"]["hold_expires_at"]) == lease.hold_expires_at
    assert "token_hash" not in response.text
    assert AGENT not in response.text

    api.box["active_lease"] = None
    api.box["settlement"] = None
    binding.last_assessment = None
    binding.pr_number = 4
    empty = api.client.get(f"/api/v1/github/bounties/{binding.bounty_id}/status")
    payload = empty.json()
    assert payload["assessment"] is None
    assert payload["settlement"] is None
    assert payload["lease"] is None
    assert payload["pr_number"] == 4

    missing = api.client.get(f"/api/v1/github/bounties/{uuid.uuid4()}/status")
    _error(missing, 404, "not_found")


def _heartbeat(api, payload, **header_kwargs):
    headers = _headers(lease_header=False, **header_kwargs)
    return api.client.post("/api/v1/workers/heartbeat", headers=headers, json=payload)


def test_heartbeat_idle_extends_work_leases_and_records_presence(api, monkeypatch):
    monkeypatch.setattr(settings, "LEASE_WORK_MINUTES", 15)
    monkeypatch.setattr(settings, "WORKER_HEARTBEAT_TTL_S", 17)
    binding = api.box["binding"]
    user = api.box["user"]
    first = _lease(binding, user, hard_expires_at=FIXED + timedelta(hours=2))
    second = _lease(
        binding,
        user,
        hard_expires_at=FIXED + timedelta(minutes=5),
        work_expires_at=FIXED + timedelta(minutes=1),
    )
    submitted = _lease(
        binding, user, status="submitted", hold_expires_at=FIXED + timedelta(hours=1)
    )
    api.box["work_leases"] = [first, second, submitted]
    payload = {
        "status": "AVAILABLE_IDLE",
        "capabilities": {"languages": ["Python", "Go"]},
        "min_bounty_usd": 5,
        "max_tokens": 8000,
    }
    response = _heartbeat(api, payload)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "status",
        "ttl_s",
        "released_leases",
        "extended_leases",
        "lapsed_leases",
        "presence",
    }
    assert body["status"] == "AVAILABLE_IDLE"
    assert body["ttl_s"] == 17
    assert body["presence"] == "ok"
    assert body["released_leases"] == []
    assert body["extended_leases"] == [str(first.id), str(second.id)]
    assert first.work_expires_at == FIXED + timedelta(minutes=15)
    assert second.work_expires_at == FIXED + timedelta(minutes=5)
    assert submitted.status == "submitted"
    assert submitted.release_reason is None
    client = api.box["redis_clients"][0]
    assert client.closed is True
    key, raw, ex = client.items[0]
    assert key == f"sb:worker:{AGENT}"
    assert ex == 17
    stored = json.loads(raw)
    assert stored["status"] == "AVAILABLE_IDLE"
    assert stored["capabilities"] == {"languages": ["Python", "Go"]}
    assert stored["min_bounty_usd"] == 5
    assert stored["max_tokens"] == 8000
    assert stored["seen_at"] == "2026-06-01T12:00:00Z"
    assert api.session.commits == 1


def test_heartbeat_preempted_releases_work_leases_only(api):
    binding = api.box["binding"]
    user = api.box["user"]
    work = _lease(binding, user)
    submitted = _lease(binding, user, status="submitted")
    original = work.work_expires_at
    api.box["work_leases"] = [work, submitted]
    response = _heartbeat(
        api,
        {
            "status": "BUSY_PREEMPTED",
            "capabilities": None,
            "min_bounty_usd": None,
            "max_tokens": None,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "BUSY_PREEMPTED"
    assert body["presence"] == "ok"
    assert body["extended_leases"] == []
    assert body["released_leases"] == [str(work.id)]
    assert work.status == "released"
    assert work.release_reason == "preempted"
    assert work.work_expires_at == original
    assert submitted.status == "submitted"
    stored = json.loads(api.box["redis_clients"][0].items[0][1])
    assert stored["status"] == "BUSY_PREEMPTED"
    assert stored["capabilities"] is None


def test_heartbeat_redis_unavailable_still_extends(api, monkeypatch):
    monkeypatch.setattr(settings, "LEASE_WORK_MINUTES", 15)
    lease = _lease(api.box["binding"], api.box["user"])
    api.box["work_leases"] = [lease]
    api.box["redis_error"] = True
    response = _heartbeat(api, {"status": "AVAILABLE_IDLE"})
    assert response.status_code == 200
    body = response.json()
    assert body["presence"] == "unavailable"
    assert body["extended_leases"] == [str(lease.id)]
    assert lease.work_expires_at == FIXED + timedelta(minutes=15)
    assert api.session.commits == 1
    assert api.box["redis_clients"][0].items == []
    assert api.box["redis_clients"][0].closed is True


def test_heartbeat_rejects_bad_auth_and_body(api):
    missing = api.client.post("/api/v1/workers/heartbeat", json={"status": "AVAILABLE_IDLE"})
    _error(missing, 401, "invalid_token")
    bad = _heartbeat(api, {"status": "OFFLINE"})
    _error(bad, 422, "invalid_body")
    garbage = api.client.post(
        "/api/v1/workers/heartbeat",
        headers={**_headers(lease_header=False), "Content-Type": "application/json"},
        content=b"not-json",
    )
    _error(garbage, 422, "invalid_body")
    assert api.session.commits == 0


@pytest.mark.parametrize(
    "route",
    ["claim", "renew", "release", "submit", "heartbeat"],
)
def test_mutating_routes_require_financial_scope(api, route):
    api.box["scopes"] = frozenset({"mcp:read"})
    lease = api.box["lease"]
    bounty_id = api.box["binding"].bounty_id
    if route == "claim":
        response = api.client.post(
            f"/api/v1/github/bounties/{bounty_id}/claim-lease",
            headers=_headers(lease_header=False),
        )
    elif route == "renew":
        response = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    elif route == "release":
        response = api.client.post(f"/api/v1/github/leases/{lease.id}/release", headers=_headers())
    elif route == "submit":
        response = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    else:
        response = _heartbeat(api, {"status": "AVAILABLE_IDLE"})
    assert response.status_code == 403
    assert response.json() == {
        "error": "insufficient_scope",
        "required_scope": "mcp:tool:financial",
    }
    assert api.session.commits == 0
    assert api.box["events"] == []
    assert api.box["pr_calls"] == []
    assert lease.status == "work"


def test_invalid_token_is_rejected_before_scope(api):
    api.box["scopes"] = frozenset()
    response = api.client.post(
        f"/api/v1/github/bounties/{api.box['binding'].bounty_id}/claim-lease",
        headers=_headers(bearer="bad", lease_header=False),
    )
    _error(response, 401, "invalid_token")


def test_claim_locks_agent_before_counting(api):
    response = api.client.post(
        f"/api/v1/github/bounties/{api.box['binding'].bounty_id}/claim-lease",
        headers=_headers(lease_header=False),
    )
    assert response.status_code == 201
    events = api.box["events"]
    assert ("lock", AGENT) in events
    assert events.index(("lock", AGENT)) < events.index(("count", AGENT))
    assert events.index(("lock", AGENT)) < events.index(("expiries", AGENT))


def test_renew_rejects_soft_expired_deadlines(api):
    lease = api.box["lease"]
    lease.work_expires_at = FIXED - timedelta(seconds=1)
    lease.hard_expires_at = FIXED + timedelta(hours=1)
    stale_work = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    _error(stale_work, 409, "lease_not_active")
    assert lease.work_expires_at == FIXED - timedelta(seconds=1)

    lease.work_expires_at = FIXED + timedelta(minutes=5)
    lease.hard_expires_at = FIXED - timedelta(seconds=1)
    stale_hard = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    _error(stale_hard, 409, "lease_not_active")
    assert lease.work_expires_at == FIXED + timedelta(minutes=5)
    assert api.session.commits == 0


def test_renew_rechecks_deadline_after_lock(api, monkeypatch):
    lease = api.box["lease"]
    original = lease.work_expires_at

    async def lock_lease(db, lease_id):
        api.box["lock_calls"].append(lease_id)
        lease.work_expires_at = FIXED - timedelta(seconds=1)
        return lease

    monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
    response = api.client.post(f"/api/v1/github/leases/{lease.id}/renew", headers=_headers())
    _error(response, 409, "lease_not_active")
    assert lease.work_expires_at == FIXED - timedelta(seconds=1)
    assert lease.work_expires_at != original
    assert api.session.commits == 0
    assert api.box["lock_calls"] == [lease.id]


def test_release_rechecks_status_after_lock(api, monkeypatch):
    lease = api.box["lease"]

    async def lock_lease(db, lease_id):
        lease.status = "expired"
        return lease

    monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
    response = api.client.post(f"/api/v1/github/leases/{lease.id}/release", headers=_headers())
    _error(response, 409, "lease_not_active")
    assert lease.status == "expired"
    assert lease.release_reason is None
    assert api.session.commits == 0


def test_submit_rechecks_window_after_github(api, monkeypatch):
    lease = api.box["lease"]
    seen = {}

    async def lock_lease(db, lease_id):
        seen["pr_calls"] = list(api.box["pr_calls"])
        lease.work_expires_at = FIXED - timedelta(seconds=1)
        return lease

    monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
    response = _submit(api, lease, {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(response, 409, "lease_not_active")
    assert seen["pr_calls"] == [(99, REPO, 7)]
    assert lease.status == "work"
    assert api.box["binding"].pr_number is None
    assert api.session.commits == 0


def test_submit_uses_claim_snapshot_not_current_identity(api):
    api.box["identity"] = None
    api.box["lease"].github_user_id = 4242
    response = _submit(api, api.box["lease"], {"pr_url": f"https://github.com/{REPO}/pull/7"})
    assert response.status_code == 200

    api.session.commits = 0
    api.box["lease"].status = "work"
    api.box["lease"].github_user_id = 7
    api.box["identity"] = _identity(api.box["user"], github_user_id=4242)
    mismatch = _submit(api, api.box["lease"], {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(mismatch, 422, "pr_author_mismatch")
    assert api.box["lease"].status == "work"


@pytest.mark.parametrize(
    "created_at",
    [
        "missing",
        None,
        "",
        "not-a-timestamp",
        "2026-06-01T12:00:00",
        "2026-06-01T11:59:59Z",
        "2026-05-01T12:00:00+00:00",
    ],
)
def test_submit_rejects_pr_that_predates_the_lease(api, created_at):
    pull = dict(api.box["pull"])
    if created_at == "missing":
        pull.pop("created_at", None)
    else:
        pull["created_at"] = created_at
    api.box["pull"] = pull
    response = _submit(api, api.box["lease"], {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(response, 422, "pr_predates_lease")
    assert api.box["lease"].status == "work"
    assert api.session.commits == 0


def test_submit_accepts_pr_opened_at_the_lease_instant(api):
    api.box["pull"] = {**api.box["pull"], "created_at": "2026-06-01T07:00:00-05:00"}
    response = _submit(api, api.box["lease"], {"pr_url": f"https://github.com/{REPO}/pull/7"})
    assert response.status_code == 200


def test_submit_integrity_error_is_pr_already_bound(api):
    api.session.commit_error = IntegrityError("UPDATE", {}, Exception("duplicate key"))
    response = _submit(api, api.box["lease"], {"pr_url": f"https://github.com/{REPO}/pull/7"})
    _error(response, 409, "pr_already_bound")
    assert api.session.rollbacks == 1
    assert api.session.commits == 1


def test_heartbeat_does_not_extend_a_soft_expired_lease(api, monkeypatch):
    monkeypatch.setattr(settings, "LEASE_WORK_MINUTES", 15)
    binding = api.box["binding"]
    user = api.box["user"]
    live = _lease(binding, user, work_expires_at=FIXED + timedelta(minutes=5))
    original = live.work_expires_at
    stale = _lease(
        binding,
        user,
        work_expires_at=FIXED + timedelta(minutes=5),
        hard_expires_at=FIXED + timedelta(hours=1),
    )
    api.box["work_leases"] = [live, stale]

    async def lock_lease(db, lease_id):
        api.box["lock_calls"].append(lease_id)
        if lease_id == stale.id:
            stale.work_expires_at = FIXED - timedelta(seconds=1)
        return live if lease_id == live.id else stale

    monkeypatch.setattr(github_leases, "lock_lease", lock_lease)
    response = _heartbeat(api, {"status": "AVAILABLE_IDLE"})
    assert response.status_code == 200
    body = response.json()
    assert body["extended_leases"] == [str(live.id)]
    assert body["lapsed_leases"] == [str(stale.id)]
    assert live.work_expires_at > original
    assert stale.work_expires_at == FIXED - timedelta(seconds=1)
    assert stale.status == "work"
    assert api.box["lock_calls"] == [live.id, stale.id]


def test_heartbeat_preempt_releases_soft_expired_work_lease(api):
    lease = _lease(
        api.box["binding"],
        api.box["user"],
        work_expires_at=FIXED - timedelta(seconds=1),
    )
    api.box["work_leases"] = [lease]
    response = _heartbeat(api, {"status": "BUSY_PREEMPTED"})
    assert response.status_code == 200
    assert response.json()["released_leases"] == [str(lease.id)]
    assert lease.status == "released"
    assert lease.release_reason == "preempted"
