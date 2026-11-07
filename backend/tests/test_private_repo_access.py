"""Private-repo bounty visibility. SQLite stand-ins; GitHub collaborator lookups are mocked."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user, get_optional_user
from app.models.bounty import Bounty, BountyMode, BountyStatus, Difficulty, ProvenanceTier
from app.models.category import Category
from app.models.claim import Claim
from app.models.github import (
    AcceptanceSpec,
    GitHubBountyBinding,
    GitHubIdentity,
    GitHubInstallation,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
    LeaseStatus,
)
from app.models.submission import Submission, SubmissionStatus
from app.models.user import User, UserType
from app.routes.bounties import router as bounty_router
from app.routes.claims import router as claims_router
from app.routes.github_leases import router as lease_router
from app.routes.submissions import public_router, router as submission_router
from app.services import github_app, github_leases, trust_gateway
from app.services.repo_visibility import (
    READ_PERMISSIONS,
    bounty_public_clause,
    bounty_visible_clause,
    has_repo_read,
    readable_private_repos,
    reset_repo_access_cache,
)
from app.services.trust_gateway import AgentClaims, TrustTokenError

_NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
PUBLIC_REWARD = 867530
PRIVATE_REWARD = 424242
READER_GITHUB_ID = 2002
MISMATCH_GITHUB_ID = 5005
_BOUNTIES = (
    "CREATE TABLE bounties ("
    "id CHAR(32) PRIMARY KEY, requester_id CHAR(32) NOT NULL, title VARCHAR(500) NOT NULL, "
    "description TEXT NOT NULL, category_id CHAR(32), tags TEXT, acceptance_criteria TEXT, "
    "reward_amount INTEGER NOT NULL, escrow_id VARCHAR(255), status VARCHAR(32) NOT NULL, "
    "deadline DATETIME, max_claims INTEGER, min_reputation FLOAT, difficulty VARCHAR(32) NOT NULL, "
    "auto_approve BOOLEAN, provenance_tier VARCHAR(64) NOT NULL, created_at DATETIME, "
    "updated_at DATETIME, funded_at DATETIME, completed_at DATETIME, expired_at DATETIME, "
    "settlement_structure TEXT, mode VARCHAR(32) NOT NULL)"
)


def _run(coro):
    return asyncio.run(coro)


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()

    async def get(self, model, ident, **kwargs):
        return self.sync.get(model, ident)

    async def flush(self):
        self.sync.flush()


class _IdentityResult:
    def __init__(self, identity):
        self._identity = identity

    def scalar_one_or_none(self):
        return self._identity


class _IdentityDb:
    def __init__(self, identity):
        self.identity = identity

    async def execute(self, statement, *args, **kwargs):
        return _IdentityResult(self.identity)


@pytest.fixture(autouse=True)
def _clear_access_cache():
    _run(reset_repo_access_cache())
    yield
    _run(reset_repo_access_cache())


def _patch_collaborator(monkeypatch, script):
    calls = []

    async def get_collaborator_record(installation_id, repo, username):
        calls.append((installation_id, repo, username))
        result = script["result"]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(github_app, "get_collaborator_record", get_collaborator_record)
    return calls


def _private_link():
    return SimpleNamespace(
        visibility="private",
        installation_id=9,
        repo_full_name="acme/secret",
        enabled=True,
    )


def _reader():
    user = SimpleNamespace(id=uuid.uuid4())
    identity = SimpleNamespace(user_id=user.id, github_user_id=42, login="octo")
    return user, identity


@pytest.mark.parametrize("permission", sorted(READ_PERMISSIONS))
def test_read_level_permission_requires_matching_github_user_id(monkeypatch, permission):
    user, identity = _reader()
    calls = _patch_collaborator(monkeypatch, {"result": (permission, 42)})
    assert _run(has_repo_read(_IdentityDb(identity), user, _private_link())) is True
    assert _run(has_repo_read(_IdentityDb(identity), user, _private_link())) is True
    assert calls == [(9, "acme/secret", "octo")]


def test_public_link_skips_github_even_without_a_user(monkeypatch):
    calls = _patch_collaborator(monkeypatch, {"result": RuntimeError("called")})
    link = _private_link()
    link.visibility = "public"
    assert _run(has_repo_read(_IdentityDb(None), None, link)) is True
    assert calls == []


def test_missing_user_link_or_identity_is_denied_without_github(monkeypatch):
    user, identity = _reader()
    calls = _patch_collaborator(monkeypatch, {"result": RuntimeError("called")})
    assert _run(has_repo_read(_IdentityDb(identity), None, _private_link())) is False
    assert _run(has_repo_read(_IdentityDb(identity), user, None)) is False
    assert _run(has_repo_read(_IdentityDb(None), user, _private_link())) is False
    assert calls == []


def test_mismatched_github_user_id_is_cached_denial(monkeypatch):
    user, identity = _reader()
    script = {"result": ("read", 999)}
    calls = _patch_collaborator(monkeypatch, script)
    db = _IdentityDb(identity)
    link = _private_link()
    assert _run(has_repo_read(db, user, link)) is False
    script["result"] = ("read", 42)
    assert _run(has_repo_read(db, user, link)) is False
    assert len(calls) == 1
    _run(reset_repo_access_cache())
    assert _run(has_repo_read(db, user, link)) is True
    assert len(calls) == 2


def test_permission_none_is_cached_denial(monkeypatch):
    user, identity = _reader()
    script = {"result": ("none", None)}
    calls = _patch_collaborator(monkeypatch, script)
    db = _IdentityDb(identity)
    link = _private_link()
    assert _run(has_repo_read(db, user, link)) is False
    script["result"] = ("admin", 42)
    assert _run(has_repo_read(db, user, link)) is False
    assert len(calls) == 1


def test_read_permission_without_user_id_is_not_cached(monkeypatch):
    user, identity = _reader()
    script = {"result": ("read", None)}
    calls = _patch_collaborator(monkeypatch, script)
    db = _IdentityDb(identity)
    link = _private_link()
    assert _run(has_repo_read(db, user, link)) is False
    script["result"] = ("read", 42)
    assert _run(has_repo_read(db, user, link)) is True
    assert len(calls) == 2


def test_github_api_error_is_denied_and_not_cached(monkeypatch):
    user, identity = _reader()
    state = {"n": 0}

    async def get_collaborator_record(installation_id, repo, username):
        state["n"] += 1
        if state["n"] == 1:
            raise github_app.GitHubAPIError("down", 503)
        return ("read", 42)

    monkeypatch.setattr(github_app, "get_collaborator_record", get_collaborator_record)
    db = _IdentityDb(identity)
    link = _private_link()
    assert _run(has_repo_read(db, user, link)) is False
    assert _run(has_repo_read(db, user, link)) is True
    assert state["n"] == 2


def test_other_exceptions_are_denied_and_not_cached(monkeypatch):
    user, identity = _reader()
    state = {"n": 0}

    async def get_collaborator_record(installation_id, repo, username):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("socket")
        return ("maintain", 42)

    monkeypatch.setattr(github_app, "get_collaborator_record", get_collaborator_record)
    assert _run(has_repo_read(_IdentityDb(identity), user, _private_link())) is False
    assert _run(has_repo_read(_IdentityDb(identity), user, _private_link())) is True
    assert state["n"] == 2


def _engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with eng.begin() as conn:
        conn.execute(text(_BOUNTIES))
    for model in (
        User,
        Category,
        GitHubIdentity,
        GitHubInstallation,
        GitHubRepoLink,
        GitHubBountyBinding,
        AcceptanceSpec,
        GitHubLease,
        GitHubSettlement,
        Claim,
        Submission,
    ):
        model.__table__.create(eng)
    return eng


def _person(name, github_id, *, kind=UserType.AGENT_OPERATOR, bot=None):
    user = User(
        id=uuid.uuid4(),
        email=f"{name}@example.com",
        display_name=name,
        user_type=kind,
        exchange_bot_id=f"{name}-agent" if bot is None else bot,
    )
    identity = None
    if github_id is not None:
        identity = GitHubIdentity(
            id=uuid.uuid4(),
            user_id=user.id,
            github_user_id=github_id,
            login=name,
            verified_at=_NOW,
        )
    return user, identity


def _bounty(requester_id, title, reward):
    return Bounty(
        id=uuid.uuid4(),
        requester_id=requester_id,
        title=title,
        description=f"secret-{title}",
        reward_amount=reward,
        status=BountyStatus.OPEN,
        difficulty=Difficulty.MEDIUM,
        provenance_tier=ProvenanceTier.TIER1_SELF_DECLARED,
        max_claims=1,
        auto_approve=False,
        created_at=_NOW,
        updated_at=_NOW,
        mode=BountyMode.PRODUCTION,
    )


def _link(repo, visibility, funder_id, *, enabled=True):
    return GitHubRepoLink(
        id=uuid.uuid4(),
        installation_id=1,
        repo_full_name=repo,
        funder_user_id=funder_id,
        enabled=enabled,
        default_policy={},
        required_checks=[],
        visibility=visibility,
    )


def _bind(sync, bounty, repo, issue):
    binding = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=bounty.id,
        repo_full_name=repo,
        issue_number=issue,
        state="advertised",
        created_at=_NOW,
    )
    sync.add(binding)
    sync.flush()
    spec = AcceptanceSpec(
        id=uuid.uuid4(),
        binding_id=binding.id,
        version=1,
        content_hash="ab" * 32,
        test_command="pytest -q",
        required_checks=[],
        policy={},
        payment_trigger="merge_with_checks",
        amount_ate=2500,
        fee_bps=0,
        required_kya=0,
        required_ema=0.0,
        security_tier=False,
    )
    sync.add(spec)
    sync.flush()
    binding.current_spec_id = spec.id
    return binding, spec


def _seed(sync: Session) -> dict:
    people = {
        "requester": _person("requester", None, kind=UserType.REQUESTER),
        "funder": _person("funder", 1001),
        "reader": _person("reader", READER_GITHUB_ID),
        "no_github": _person("no_github", None),
        "outsider": _person("outsider", 3003),
        "lessee": _person("lessee", 4004),
        "mismatch": _person("mismatch", MISMATCH_GITHUB_ID),
        "submitter": _person("submitter", 6006),
        "dup_a": _person("dup_a", None, bot="dup-agent"),
        "dup_b": _person("dup_b", None, bot="dup-agent"),
    }
    users = {name: user for name, (user, _identity) in people.items()}
    sync.add_all(users.values())
    sync.add(GitHubInstallation(id=uuid.uuid4(), installation_id=1, account_login="acme"))
    sync.add_all(identity for _user, identity in people.values() if identity is not None)
    sync.flush()
    funder_id = users["funder"].id
    requester_id = users["requester"].id
    bounties = {
        "public": _bounty(requester_id, "Public bounty", PUBLIC_REWARD),
        "plain": _bounty(requester_id, "Plain bounty", 50),
        "private": _bounty(requester_id, "Private bounty", PRIVATE_REWARD),
        "held": _bounty(requester_id, "Held bounty", 111),
        "disabled": _bounty(requester_id, "Disabled bounty", 222),
    }
    sync.add_all(bounties.values())
    sync.add_all(
        [
            _link("acme/public", "public", funder_id),
            _link("acme/private", "private", funder_id),
            _link("acme/disabled", "private", funder_id, enabled=False),
        ]
    )
    sync.flush()
    public_binding, _public_spec = _bind(sync, bounties["public"], "acme/public", 1)
    _bind(sync, bounties["private"], "acme/private", 1)
    held_binding, held_spec = _bind(sync, bounties["held"], "acme/private", 2)
    _bind(sync, bounties["disabled"], "acme/disabled", 1)
    sync.add(
        GitHubLease(
            id=uuid.uuid4(),
            binding_id=held_binding.id,
            agent_id=users["lessee"].exchange_bot_id,
            user_id=users["lessee"].id,
            github_user_id=4004,
            spec_id=held_spec.id,
            token_hash="c" * 64,
            status=LeaseStatus.WORK.value,
            work_expires_at=_NOW + timedelta(hours=1),
            hard_expires_at=_NOW + timedelta(hours=2),
            created_at=_NOW,
        )
    )
    tokens = {"private": uuid.uuid4(), "public": uuid.uuid4()}
    private_submission = uuid.uuid4()
    sync.add_all(
        [
            Submission(
                id=private_submission,
                claim_id=uuid.uuid4(),
                bounty_id=bounties["private"].id,
                agent_user_id=users["submitter"].id,
                deliverable={"content": "patch", "content_type": "text/plain"},
                status=SubmissionStatus.APPROVED,
                public_share=True,
                share_token=tokens["private"],
                submitted_at=_NOW,
                score=90,
            ),
            Submission(
                id=uuid.uuid4(),
                claim_id=uuid.uuid4(),
                bounty_id=bounties["public"].id,
                agent_user_id=users["submitter"].id,
                deliverable={"content": "public patch", "content_type": "text/plain"},
                status=SubmissionStatus.APPROVED,
                public_share=True,
                share_token=tokens["public"],
                submitted_at=_NOW,
                score=80,
            ),
        ]
    )
    sync.commit()
    return {
        "users": users,
        "ids": {name: bounty.id for name, bounty in bounties.items()},
        "tokens": tokens,
        "submission_id": private_submission,
        "public_binding": public_binding.id,
    }


@pytest.fixture
def world(monkeypatch):
    eng = _engine()
    sync = Session(eng, expire_on_commit=False)
    catalog = _seed(sync)
    bridge = _Bridge(sync)
    holder = {"user": None}
    catalog.update(
        {
            "db": bridge,
            "holder": holder,
            "github_calls": [],
            "scopes": frozenset({"mcp:read"}),
            "collaborators": {
                "reader": ("read", READER_GITHUB_ID),
                "mismatch": ("read", 111),
            },
            "collaborator_impl": None,
        }
    )

    async def get_collaborator_record(installation_id, repo, username):
        catalog["github_calls"].append((installation_id, repo, username))
        impl = catalog["collaborator_impl"]
        if impl is not None:
            return await impl(installation_id, repo, username)
        result = catalog["collaborators"].get(username, ("none", None))
        if isinstance(result, Exception):
            raise result
        return result

    def verify(token):
        if token == "bad":
            raise TrustTokenError("bad")
        return AgentClaims(
            agent_id=token,
            org_id="org-1",
            kya_level=2,
            reputation=1.0,
            scopes=catalog["scopes"],
            raw={},
        )

    async def lock_agent(db, agent_id):
        return None

    async def shares_principal(*args, **kwargs):
        return False

    monkeypatch.setattr(github_app, "get_collaborator_record", get_collaborator_record)
    monkeypatch.setattr(trust_gateway, "verify_gateway_token", verify)
    monkeypatch.setattr(github_leases, "lock_agent_for_claim", lock_agent)
    monkeypatch.setattr(github_leases, "_shares_principal", shares_principal)
    monkeypatch.setattr(settings, "GITHUB_ENFORCE_KYA_LINEAGE_SEPARATION", True)
    monkeypatch.setattr(settings, "GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM", False)

    app = FastAPI()
    app.include_router(bounty_router, prefix="/api/bounties")
    app.include_router(claims_router, prefix="/api")
    app.include_router(submission_router, prefix="/api")
    app.include_router(public_router, prefix="/api")
    app.include_router(lease_router, prefix="/api/v1")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_optional_user] = lambda: holder["user"]
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    client = TestClient(app)
    catalog["client"] = client
    try:
        yield catalog
    finally:
        client.close()
        sync.close()
        eng.dispose()


def _as(world, who):
    world["holder"]["user"] = None if who is None else world["users"][who]


def _titles(response):
    return {item["title"] for item in response.json()["bounties"]}


def _bearer(world, who):
    return {"Authorization": f"Bearer {world['users'][who].exchange_bot_id}"}


@pytest.mark.parametrize(
    ("who", "visible_private"),
    [
        (None, set()),
        ("no_github", set()),
        ("outsider", set()),
        ("mismatch", set()),
        ("funder", set()),
        ("lessee", set()),
        ("reader", {"Private bounty", "Held bounty"}),
        ("requester", {"Private bounty", "Held bounty", "Disabled bounty"}),
    ],
)
def test_marketplace_list_visibility(world, who, visible_private):
    _as(world, who)
    response = world["client"].get("/api/bounties")
    assert response.status_code == 200
    titles = _titles(response)
    assert {"Public bounty", "Plain bounty"} <= titles
    for title in ("Private bounty", "Held bounty", "Disabled bounty"):
        assert (title in titles) is (title in visible_private)
    if who == "reader":
        by_title = {item["title"]: item["github_visibility"] for item in response.json()["bounties"]}
        assert by_title["Private bounty"] == "private"
        assert by_title["Held bounty"] == "private"
        assert by_title["Public bounty"] == "public"
        assert by_title["Plain bounty"] is None
        assert {repo for _install, repo, _login in world["github_calls"]} == {"acme/private"}


@pytest.mark.parametrize(
    ("who", "bounty", "ok"),
    [
        (None, "private", False),
        ("no_github", "private", False),
        ("outsider", "private", False),
        ("mismatch", "private", False),
        ("lessee", "private", False),
        ("reader", "private", True),
        ("funder", "private", True),
        ("requester", "private", True),
        (None, "held", False),
        ("outsider", "held", False),
        ("lessee", "held", True),
        ("reader", "held", True),
        ("funder", "held", True),
        ("reader", "disabled", False),
        ("funder", "disabled", True),
        ("requester", "disabled", True),
        (None, "public", True),
        ("outsider", "public", True),
        (None, "plain", True),
    ],
)
def test_detail_visibility(world, who, bounty, ok):
    _as(world, who)
    response = world["client"].get(f"/api/bounties/{world['ids'][bounty]}")
    if ok:
        assert response.status_code == 200
        body = response.json()
        expected = {
            "public": "public",
            "plain": None,
            "private": "private",
            "held": "private",
            "disabled": "private",
        }[bounty]
        assert body["github_visibility"] == expected
        assert body["title"]
    else:
        assert response.status_code == 404
        assert response.json() == {"detail": "Bounty not found"}
        assert "secret-" not in response.text
        assert str(PRIVATE_REWARD) not in response.text


def test_funder_and_lessee_detail_does_not_call_github(world):
    _as(world, "funder")
    funder = world["client"].get(f"/api/bounties/{world['ids']['private']}")
    assert funder.status_code == 200
    _as(world, "lessee")
    lessee = world["client"].get(f"/api/bounties/{world['ids']['held']}")
    assert lessee.status_code == 200
    assert world["github_calls"] == []


def test_public_detail_does_not_call_github(world):
    _as(world, "outsider")
    response = world["client"].get(f"/api/bounties/{world['ids']['public']}")
    assert response.status_code == 200
    assert response.json()["github_visibility"] == "public"
    assert world["github_calls"] == []


def test_bounty_public_clause_still_excludes_private(world):
    public_ids = set(_run(world["db"].execute(select(Bounty.id).where(bounty_public_clause()))).scalars())
    assert world["ids"]["public"] in public_ids
    assert world["ids"]["plain"] in public_ids
    assert world["ids"]["private"] not in public_ids
    assert world["ids"]["held"] not in public_ids
    assert world["ids"]["disabled"] not in public_ids
    widened = bounty_visible_clause({"acme/private"})
    visible_ids = set(_run(world["db"].execute(select(Bounty.id).where(widened))).scalars())
    assert world["ids"]["private"] in visible_ids
    assert world["ids"]["held"] in visible_ids
    assert world["ids"]["disabled"] not in visible_ids
    assert world["ids"]["public"] in visible_ids


def test_readable_private_repos_skips_public_and_disabled(world):
    funder_id = world["users"]["funder"].id
    world["db"].sync.add_all(
        [
            _link("acme/internal", "internal", funder_id),
            _link("acme/unknown", None, funder_id),
        ]
    )
    world["db"].sync.commit()
    repos = _run(readable_private_repos(world["db"], world["users"]["reader"]))
    assert repos == {"acme/private", "acme/internal", "acme/unknown"}
    called = {repo for _install, repo, _login in world["github_calls"]}
    assert called == repos
    world["github_calls"].clear()
    assert _run(readable_private_repos(world["db"], None)) == set()
    assert _run(readable_private_repos(world["db"], world["users"]["no_github"])) == set()
    assert world["github_calls"] == []


def test_lease_list_without_a_token_is_public_only(world):
    listed = world["client"].get("/api/v1/github/bounties")
    assert listed.status_code == 200
    assert _titles(listed) == {"Public bounty"}
    assert listed.json()["bounties"][0]["visibility"] == "public"
    status = world["client"].get(f"/api/v1/github/bounties/{world['ids']['private']}/status")
    assert status.status_code == 404
    assert status.json() == {"error": "not_found"}
    assert str(PRIVATE_REWARD) not in status.text


def test_lease_reads_reject_an_invalid_token(world):
    headers = {"Authorization": "Bearer bad"}
    listed = world["client"].get("/api/v1/github/bounties", headers=headers)
    assert listed.status_code == 401
    assert listed.json() == {"error": "invalid_token"}
    status = world["client"].get(
        f"/api/v1/github/bounties/{world['ids']['private']}/status",
        headers=headers,
    )
    assert status.status_code == 401
    assert status.json() == {"error": "invalid_token"}


def test_reader_lease_list_includes_private_without_read_scope(world):
    world["scopes"] = frozenset()
    listed = world["client"].get("/api/v1/github/bounties", headers=_bearer(world, "reader"))
    assert listed.status_code == 200
    assert _titles(listed) == {"Public bounty", "Private bounty"}
    by_title = {item["title"]: item["visibility"] for item in listed.json()["bounties"]}
    assert by_title == {"Public bounty": "public", "Private bounty": "private"}
    status = world["client"].get(
        f"/api/v1/github/bounties/{world['ids']['private']}/status",
        headers=_bearer(world, "reader"),
    )
    assert status.status_code == 200
    assert status.json()["bounty_id"] == str(world["ids"]["private"])


@pytest.mark.parametrize("who", ["funder", "lessee", "requester", "outsider", "no_github", "mismatch"])
def test_lease_list_without_read_stays_public(world, who):
    listed = world["client"].get("/api/v1/github/bounties", headers=_bearer(world, who))
    assert listed.status_code == 200
    assert _titles(listed) == {"Public bounty"}


def test_unmapped_agent_is_public_only(world):
    for token in ("nobody-agent", "dup-agent"):
        headers = {"Authorization": f"Bearer {token}"}
        listed = world["client"].get("/api/v1/github/bounties", headers=headers)
        assert listed.status_code == 200
        assert _titles(listed) == {"Public bounty"}
        status = world["client"].get(
            f"/api/v1/github/bounties/{world['ids']['private']}/status",
            headers=headers,
        )
        assert status.status_code == 404
        assert status.json() == {"error": "not_found"}


@pytest.mark.parametrize(
    ("who", "bounty", "ok"),
    [
        (None, "private", False),
        ("no_github", "private", False),
        ("outsider", "private", False),
        ("mismatch", "private", False),
        ("lessee", "private", False),
        ("reader", "private", True),
        ("funder", "private", True),
        ("requester", "private", True),
        (None, "held", False),
        ("lessee", "held", True),
        ("reader", "held", True),
        ("funder", "held", True),
        (None, "public", True),
        ("reader", "disabled", False),
        ("funder", "disabled", True),
    ],
)
def test_lease_status_visibility(world, who, bounty, ok):
    headers = {} if who is None else _bearer(world, who)
    response = world["client"].get(
        f"/api/v1/github/bounties/{world['ids'][bounty]}/status",
        headers=headers,
    )
    if ok:
        assert response.status_code == 200
        assert response.json()["bounty_id"] == str(world["ids"][bounty])
    else:
        assert response.status_code == 404
        assert response.json() == {"error": "not_found"}
        assert str(PRIVATE_REWARD) not in response.text
        assert "secret-" not in response.text


def test_mismatched_id_stays_hidden_on_the_lease_list(world):
    headers = _bearer(world, "mismatch")
    first = world["client"].get("/api/v1/github/bounties", headers=headers)
    assert _titles(first) == {"Public bounty"}
    calls = len(world["github_calls"])
    assert calls >= 1
    world["collaborators"]["mismatch"] = ("read", MISMATCH_GITHUB_ID)
    second = world["client"].get("/api/v1/github/bounties", headers=headers)
    assert _titles(second) == {"Public bounty"}
    assert len(world["github_calls"]) == calls


def test_github_error_on_the_lease_list_is_not_cached(world):
    state = {"fail": True}

    async def impl(installation_id, repo, username):
        if state["fail"]:
            raise github_app.GitHubAPIError("down", 503)
        return ("read", READER_GITHUB_ID)

    world["collaborator_impl"] = impl
    headers = _bearer(world, "reader")
    hidden = world["client"].get("/api/v1/github/bounties", headers=headers)
    assert hidden.status_code == 200
    assert "Private bounty" not in _titles(hidden)
    state["fail"] = False
    shown = world["client"].get("/api/v1/github/bounties", headers=headers)
    assert "Private bounty" in _titles(shown)
    by_title = {item["title"]: item["visibility"] for item in shown.json()["bounties"]}
    assert by_title["Private bounty"] == "private"


def _claim(world, who, bounty):
    return world["client"].post(
        f"/api/v1/github/bounties/{world['ids'][bounty]}/claim-lease",
        headers=_bearer(world, who),
    )


def test_claim_visibility(world):
    anonymous = world["client"].post(
        f"/api/v1/github/bounties/{world['ids']['private']}/claim-lease"
    )
    assert anonymous.status_code == 401
    assert anonymous.json() == {"error": "invalid_token"}

    outsider = _claim(world, "outsider", "private")
    assert outsider.status_code == 404
    assert outsider.json()["error"] == "not_found"
    assert "secret-" not in outsider.text

    mismatch = _claim(world, "mismatch", "private")
    assert mismatch.status_code == 404

    missing_identity = _claim(world, "no_github", "private")
    assert missing_identity.status_code == 404
    assert missing_identity.json()["error"] == "not_found"

    lessee_open = _claim(world, "lessee", "private")
    assert lessee_open.status_code == 404

    lessee_held = _claim(world, "lessee", "held")
    assert lessee_held.status_code == 409
    assert lessee_held.json()["error"] == "lease_held"

    funder = _claim(world, "funder", "private")
    assert funder.status_code == 403
    assert funder.json()["error"] == "self_dealing"

    before = _run(
        world["db"].execute(select(func.count()).select_from(GitHubLease))
    ).scalar_one()
    reader = _claim(world, "reader", "private")
    assert reader.status_code == 201, reader.text
    body = reader.json()
    assert body["repo"] == "acme/private"
    assert body["bounty_id"] == str(world["ids"]["private"])
    after = _run(
        world["db"].execute(select(func.count()).select_from(GitHubLease))
    ).scalar_one()
    assert after == before + 1


def test_submission_is_hidden_from_a_stranger(world):
    path = f"/api/submissions/{world['submission_id']}"
    _as(world, "outsider")
    denied = world["client"].get(path)
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Submission not found"}
    assert "secret-" not in denied.text
    _as(world, "submitter")
    allowed = world["client"].get(path)
    assert allowed.status_code == 200
    assert allowed.json()["bounty_id"] == str(world["ids"]["private"])
    _as(world, "reader")
    assert world["client"].get(path).status_code == 200


def test_marketplace_claim_404_for_a_stranger(world, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("claim should not be created")

    monkeypatch.setattr("app.routes.claims.exchange_svc.create_escrow", explode)
    monkeypatch.setattr("app.routes.claims.claim_service.create_claim", explode)
    _as(world, "outsider")
    response = world["client"].post(
        f"/api/bounties/{world['ids']['private']}/claim",
        json={},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Bounty not found"}
    assert "secret-" not in response.text
    assert str(PRIVATE_REWARD) not in response.text


def test_private_badge_is_404_and_omits_the_reward(world):
    private = world["client"].get(f"/api/share/{world['tokens']['private']}/badge.svg")
    assert private.status_code == 404
    assert "svg" not in private.headers.get("content-type", "")
    assert str(PRIVATE_REWARD) not in private.text
    assert "secret-" not in private.text
    public = world["client"].get(f"/api/share/{world['tokens']['public']}/badge.svg")
    assert public.status_code == 200
    assert "svg" in public.headers["content-type"]
    assert str(PUBLIC_REWARD) in public.text
