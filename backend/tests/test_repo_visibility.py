"""Private-repo bounty hiding. Clauses compile to PostgreSQL; rows use a slim bounties table.

The Bounty model uses Postgres ARRAY and Enum columns, so these tests never call
Bounty.__table__.create. The stand-in table has only the columns the visibility
queries read.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.middleware.auth import get_optional_user
from app.models.bounty import Bounty, BountyStatus, Difficulty, ProvenanceTier
from app.models.github import (
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubLease,
    GitHubRepoLink,
    LeaseStatus,
)
from app.models.submission import Submission, SubmissionStatus
from app.models.user import User, UserType
from app.routes.bounties import router as bounty_router
from app.routes.stats import router as stats_router
from app.routes.submissions import public_router, router as submission_router
from app.services import bounty_service, github_leases, solver_setup
from app.services.repo_visibility import (
    bounty_hidden_clause,
    bounty_public_clause,
    can_view_bounty,
    hidden_bounty_ids,
)

HIDDEN_NAMES = (
    "null",
    "internal",
    "private",
    "missing",
    "submitted",
    "released",
    "expired",
    "mixed",
    "capital",
)
_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _pg(stmt) -> str:
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return " ".join(str(compiled).split())


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)


class _CaptureResult:
    def scalar(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return []


class _Capture:
    def __init__(self):
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _CaptureResult()


class _IdRows:
    def __init__(self, ids):
        self._ids = list(ids)

    def scalars(self):
        return self

    def all(self):
        return list(self._ids)


class _First:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


def _view(bounty_id, requester_id, name: str):
    return SimpleNamespace(
        id=bounty_id,
        requester_id=requester_id,
        title=f"{name} title",
        description=f"{name} description",
        category_id=None,
        category=None,
        tags=None,
        acceptance_criteria=None,
        reward_amount=100,
        escrow_id=None,
        status=BountyStatus.OPEN,
        deadline=None,
        max_claims=1,
        min_reputation=None,
        difficulty=Difficulty.MEDIUM,
        auto_approve=False,
        provenance_tier=ProvenanceTier.TIER1_SELF_DECLARED,
        settlement_structure=None,
        created_at=_NOW,
        updated_at=_NOW,
        funded_at=None,
        completed_at=None,
    )


def _user(user_id, email: str, name: str) -> User:
    return User(
        id=user_id,
        email=email,
        display_name=name,
        user_type=UserType.REQUESTER,
    )


def _insert_bounty(db: _Bridge, bounty_id, requester_id) -> None:
    db.sync.execute(
        text("INSERT INTO bounties (id, requester_id) VALUES (:id, :rid)"),
        {"id": bounty_id.hex, "rid": requester_id.hex},
    )


def _link(db: _Bridge, repo: str, visibility, funder_id) -> None:
    db.sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=1,
            repo_full_name=repo,
            funder_user_id=funder_id,
            enabled=True,
            default_policy={},
            required_checks=[],
            visibility=visibility,
        )
    )


def _binding(db: _Bridge, bounty_id, repo: str, issue: int) -> GitHubBountyBinding:
    row = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=bounty_id,
        repo_full_name=repo,
        issue_number=issue,
        state="advertised",
    )
    db.sync.add(row)
    return row


def _lease(db: _Bridge, binding: GitHubBountyBinding, user_id, status: str) -> None:
    db.sync.add(
        GitHubLease(
            id=uuid.uuid4(),
            binding_id=binding.id,
            agent_id="agent",
            user_id=user_id,
            github_user_id=7,
            spec_id=uuid.uuid4(),
            token_hash=uuid.uuid4().hex,
            status=status,
            work_expires_at=_NOW,
            hard_expires_at=_NOW,
        )
    )


def _share(db: _Bridge, bounty_id, agent_id, token) -> None:
    db.sync.add(
        Submission(
            id=uuid.uuid4(),
            claim_id=uuid.uuid4(),
            bounty_id=bounty_id,
            agent_user_id=agent_id,
            deliverable={"content": "deliverable body", "content_type": "text/plain"},
            status=SubmissionStatus.APPROVED,
            public_share=True,
            share_token=token,
            submitted_at=_NOW,
        )
    )


def _engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE bounties ("
                "id CHAR(32) NOT NULL PRIMARY KEY, "
                "requester_id CHAR(32) NOT NULL)"
            )
        )
    for model in (
        User,
        GitHubInstallation,
        GitHubRepoLink,
        GitHubBountyBinding,
        GitHubLease,
        Submission,
    ):
        model.__table__.create(eng)
    return eng


def _seed(db: _Bridge) -> dict:
    requester, funder, solver, other = (uuid.uuid4() for _ in range(4))
    db.sync.add_all(
        [
            _user(requester, "requester@example.com", "Requester"),
            _user(funder, "funder@example.com", "Funder"),
            _user(solver, "solver@example.com", "Solver"),
            _user(other, "other@example.com", "Other"),
            GitHubInstallation(id=uuid.uuid4(), installation_id=1, account_login="acme"),
        ]
    )
    db.sync.flush()
    names = (
        "public",
        "plain",
        "null",
        "internal",
        "private",
        "missing",
        "submitted",
        "released",
        "expired",
        "mixed",
        "capital",
    )
    ids = {name: uuid.uuid4() for name in names}
    for bounty_id in ids.values():
        _insert_bounty(db, bounty_id, requester)
    _link(db, "acme/public", "public", funder)
    _link(db, "acme/null", None, funder)
    _link(db, "acme/internal", "internal", funder)
    _link(db, "acme/private", "private", funder)
    _link(db, "acme/submitted", "private", funder)
    _link(db, "acme/released", "private", funder)
    _link(db, "acme/expired", "private", funder)
    _link(db, "acme/mixed-pub", "public", funder)
    _link(db, "acme/mixed-priv", "private", funder)
    _link(db, "acme/capital", "Public", funder)
    bindings = {
        "public": _binding(db, ids["public"], "acme/public", 1),
        "null": _binding(db, ids["null"], "acme/null", 2),
        "internal": _binding(db, ids["internal"], "acme/internal", 3),
        "private": _binding(db, ids["private"], "acme/private", 4),
        "missing": _binding(db, ids["missing"], "acme/missing", 5),
        "submitted": _binding(db, ids["submitted"], "acme/submitted", 6),
        "released": _binding(db, ids["released"], "acme/released", 7),
        "expired": _binding(db, ids["expired"], "acme/expired", 8),
        "capital": _binding(db, ids["capital"], "acme/capital", 9),
    }
    _binding(db, ids["mixed"], "acme/mixed-pub", 10)
    _binding(db, ids["mixed"], "acme/mixed-priv", 11)
    _lease(db, bindings["private"], solver, LeaseStatus.WORK.value)
    _lease(db, bindings["submitted"], solver, LeaseStatus.SUBMITTED.value)
    _lease(db, bindings["released"], other, LeaseStatus.RELEASED.value)
    _lease(db, bindings["expired"], other, LeaseStatus.EXPIRED.value)
    shares = {}
    for name in ("public", "plain", "private", "null", "internal", "missing"):
        token = uuid.uuid4()
        _share(db, ids[name], solver, token)
        shares[name] = token
    db.sync.commit()
    return {
        "requester": requester,
        "funder": funder,
        "solver": solver,
        "other": other,
        "ids": ids,
        "views": {bounty_id: _view(bounty_id, requester, name) for name, bounty_id in ids.items()},
        "shares": shares,
        "seen": [],
    }


@pytest.fixture
def world(monkeypatch):
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    catalog = _seed(bridge)
    catalog["db"] = bridge

    async def fake_get_bounty(_db, bounty_id):
        catalog["seen"].append(bounty_id)
        return catalog["views"].get(bounty_id)

    monkeypatch.setattr(bounty_service, "get_bounty", fake_get_bounty)
    holder = {"user": None}

    app = FastAPI()
    app.include_router(bounty_router, prefix="/api/bounties")
    app.include_router(submission_router, prefix="/api")
    app.include_router(public_router, prefix="/api")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_optional_user] = lambda: holder["user"]
    client = TestClient(app)
    catalog["client"] = client
    catalog["holder"] = holder
    try:
        yield catalog
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _who(world, name: str | None):
    world["holder"]["user"] = None if name is None else SimpleNamespace(id=world[name])


def _get(world, path: str, who: str | None):
    _who(world, who)
    return world["client"].get(path)


def test_hidden_clause_is_exists_of_non_public_binding():
    hidden = _pg(select(Bounty.id).where(bounty_hidden_clause()))
    public = _pg(select(Bounty.id).where(bounty_public_clause()))
    assert "WHERE EXISTS (SELECT 1" in hidden
    assert "FROM github_bounty_bindings" in hidden
    assert "github_bounty_bindings.bounty_id = bounties.id" in hidden
    assert "NOT (EXISTS (SELECT 1" in hidden
    assert "FROM github_repo_links" in hidden
    assert "github_repo_links.repo_full_name = github_bounty_bindings.repo_full_name" in hidden
    assert "github_repo_links.visibility = 'public'" in hidden
    assert "IS NULL" not in hidden
    assert "WHERE NOT (EXISTS (SELECT 1" in public
    assert "visibility = 'public'" in public
    joined = (
        select(Bounty.id)
        .join(GitHubBountyBinding, GitHubBountyBinding.bounty_id == Bounty.id)
        .join(
            GitHubRepoLink,
            GitHubRepoLink.repo_full_name == GitHubBountyBinding.repo_full_name,
        )
        .where(bounty_hidden_clause())
    )
    sql = _pg(joined)
    assert sql.count("FROM github_bounty_bindings") == 1
    assert sql.count("FROM github_repo_links") == 1
    assert "github_bounty_bindings.bounty_id = bounties.id" in sql


def test_empty_hidden_bounty_ids_does_not_query():
    class _Boom:
        async def execute(self, *args, **kwargs):
            raise AssertionError("query")

    assert _run(hidden_bounty_ids(_Boom(), [])) == set()
    assert _run(hidden_bounty_ids(_Boom(), ())) == set()


def test_hidden_ids_cover_null_internal_missing_and_skip_unbound(world):
    extra = uuid.uuid4()
    ids = [*world["ids"].values(), extra, world["ids"]["private"]]
    hidden = _run(hidden_bounty_ids(world["db"], ids))
    assert hidden == {world["ids"][name] for name in HIDDEN_NAMES}
    assert all(isinstance(bounty_id, uuid.UUID) for bounty_id in hidden)


_VIEW_CASES = [
    ("public", None, True),
    ("plain", None, True),
    ("null", None, False),
    ("internal", None, False),
    ("capital", None, False),
    ("private", None, False),
    ("missing", None, False),
    ("mixed", None, False),
    ("private", "requester", True),
    ("null", "requester", True),
    ("missing", "requester", True),
    ("private", "funder", True),
    ("null", "funder", True),
    ("internal", "funder", True),
    ("missing", "funder", False),
    ("plain", "funder", True),
    ("private", "solver", True),
    ("submitted", "solver", True),
    ("null", "solver", False),
    ("released", "other", False),
    ("expired", "other", False),
    ("private", "other", False),
    ("public", "other", True),
]


@pytest.mark.parametrize(("name", "who", "expected"), _VIEW_CASES)
def test_can_view_bounty(world, name, who, expected):
    user = None if who is None else SimpleNamespace(id=world[who])
    assert _run(can_view_bounty(world["db"], world["ids"][name], user)) is expected


@pytest.mark.parametrize(("name", "who", "expected"), _VIEW_CASES)
def test_detail_404_unless_entitled(world, name, who, expected):
    bounty_id = world["ids"][name]
    response = _get(world, f"/api/bounties/{bounty_id}", who)
    if expected:
        assert response.status_code == 200
        assert response.json()["title"] == f"{name} title"
        assert response.json()["description"] == f"{name} description"
    else:
        assert response.status_code == 404
        assert response.json() == {"detail": "Bounty not found"}
        assert f"{name} title" not in response.text
        assert f"{name} description" not in response.text


def test_detail_draft_stays_owner_only_when_repo_is_public(world):
    view = world["views"][world["ids"]["plain"]]
    view.status = BountyStatus.DRAFT
    anon = _get(world, f"/api/bounties/{view.id}", None)
    stranger = _get(world, f"/api/bounties/{view.id}", "other")
    owner = _get(world, f"/api/bounties/{view.id}", "requester")
    assert anon.status_code == 404
    assert stranger.status_code == 404
    assert view.title not in anon.text
    assert owner.status_code == 200
    assert owner.json()["title"] == view.title


def test_detail_requester_can_open_hidden_draft(world):
    view = world["views"][world["ids"]["private"]]
    view.status = BountyStatus.DRAFT
    anon = _get(world, f"/api/bounties/{view.id}", None)
    owner = _get(world, f"/api/bounties/{view.id}", "requester")
    assert anon.status_code == 404
    assert view.title not in anon.text
    assert owner.status_code == 200


def test_missing_bounty_detail_is_404(world):
    response = _get(world, f"/api/bounties/{uuid.uuid4()}", "requester")
    assert response.status_code == 404
    assert response.json()["detail"] == "Bounty not found"


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("public", True),
        ("plain", True),
        ("private", False),
        ("null", False),
        ("internal", False),
        ("missing", False),
    ],
)
def test_share_404_for_hidden_bounty(world, name, ok):
    world["seen"].clear()
    response = world["client"].get(f"/api/share/{world['shares'][name]}")
    assert world["seen"] == [world["ids"][name]]
    if ok:
        assert response.status_code == 200
        body = response.json()
        assert body["bounty_title"] == f"{name} title"
        assert body["bounty_description"] == f"{name} description"
    else:
        assert response.status_code == 404
        assert response.json() == {"detail": "Shared submission not found"}
        assert f"{name} title" not in response.text
        assert f"{name} description" not in response.text


@pytest.mark.parametrize(
    ("name", "status", "who", "code"),
    [
        ("private", BountyStatus.OPEN, None, 404),
        ("private", BountyStatus.COMPLETED, None, 404),
        ("null", BountyStatus.COMPLETED, None, 404),
        ("internal", BountyStatus.COMPLETED, None, 404),
        ("missing", BountyStatus.COMPLETED, None, 404),
        ("public", BountyStatus.COMPLETED, None, 200),
        ("plain", BountyStatus.COMPLETED, None, 200),
        ("public", BountyStatus.OPEN, None, 401),
        ("plain", BountyStatus.OPEN, None, 401),
        ("private", BountyStatus.OPEN, "requester", 200),
        ("private", BountyStatus.OPEN, "funder", 200),
        ("private", BountyStatus.OPEN, "solver", 200),
        ("submitted", BountyStatus.OPEN, "solver", 200),
        ("private", BountyStatus.OPEN, "other", 404),
        ("missing", BountyStatus.OPEN, "funder", 404),
        ("missing", BountyStatus.OPEN, "requester", 200),
        ("released", BountyStatus.OPEN, "other", 404),
        ("expired", BountyStatus.OPEN, "other", 404),
        ("null", BountyStatus.OPEN, "solver", 404),
        ("mixed", BountyStatus.OPEN, None, 404),
        ("mixed", BountyStatus.OPEN, "funder", 200),
    ],
)
def test_submission_list_404_unless_can_view(world, name, status, who, code):
    view = world["views"][world["ids"][name]]
    view.status = status
    response = _get(world, f"/api/bounties/{view.id}/submissions", who)
    assert response.status_code == code
    if code == 404:
        assert response.json() == {"detail": "Bounty not found"}
        assert view.title not in response.text
    elif code == 401:
        assert response.json() == {"detail": "Authentication required"}
    else:
        body = response.json()
        assert isinstance(body, list)
        if name in world["shares"]:
            assert [row["bounty_id"] for row in body] == [str(view.id)]
        else:
            assert body == []


def _assert_public_clause(sql: str) -> None:
    assert "NOT (EXISTS (SELECT 1" in sql
    assert "github_bounty_bindings.bounty_id = bounties.id" in sql
    assert "github_repo_links.visibility = 'public'" in sql


def test_list_bounties_filters_anonymous_and_keeps_requester_rows():
    viewer = uuid.uuid4()
    anon = _Capture()
    _run(bounty_service.list_bounties(anon, search="widget"))
    assert len(anon.stmts) == 2
    for stmt in anon.stmts:
        sql = _pg(stmt)
        _assert_public_clause(sql)
        assert "requester_id =" not in sql
        assert "ILIKE" in sql

    signed_in = _Capture()
    _run(bounty_service.list_bounties(signed_in, viewer_id=viewer))
    assert len(signed_in.stmts) == 2
    for stmt in signed_in.stmts:
        sql = _pg(stmt)
        _assert_public_clause(sql)
        assert f"bounties.requester_id = '{viewer}'" in sql


def test_list_route_applies_the_same_filter():
    db = _Capture()
    holder = {"user": None}
    app = FastAPI()
    app.include_router(bounty_router, prefix="/api/bounties")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_optional_user] = lambda: holder["user"]
    client = TestClient(app)
    try:
        anon = client.get("/api/bounties")
        assert anon.status_code == 200
        assert anon.json()["bounties"] == []
        anon_sql = [_pg(stmt) for stmt in db.stmts]
        assert anon_sql
        assert all("requester_id =" not in sql for sql in anon_sql)
        assert all("visibility = 'public'" in sql for sql in anon_sql)
        db.stmts.clear()
        holder["user"] = SimpleNamespace(id=uuid.uuid4())
        signed_in = client.get("/api/bounties")
        assert signed_in.status_code == 200
        signed_sql = [_pg(stmt) for stmt in db.stmts]
        assert signed_sql
        assert all(f"requester_id = '{holder['user'].id}'" in sql for sql in signed_sql)
    finally:
        client.close()


def test_completed_results_excludes_hidden_bounties():
    db = _Capture()
    app = FastAPI()
    app.include_router(bounty_router, prefix="/api/bounties")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.get("/api/bounties/completed-results")
        assert response.status_code == 200
        assert response.json() == []
        assert len(db.stmts) == 1
        sql = _pg(db.stmts[0])
        _assert_public_clause(sql)
        assert "bounties.title" in sql
    finally:
        client.close()


def test_public_stats_exclude_hidden_bounties():
    db = _Capture()
    app = FastAPI()
    app.include_router(stats_router, prefix="/api/stats")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.get("/api/stats")
        assert response.status_code == 200
        assert response.json() == {
            "open_bounties": 0,
            "completed_bounties": 0,
            "total_settled_ate": 0,
            "active_agents": 0,
        }
        assert len(db.stmts) == 4
        open_sql, completed_sql, settled_sql, agents_sql = (_pg(stmt) for stmt in db.stmts)
        for sql in (open_sql, completed_sql, settled_sql):
            _assert_public_clause(sql)
        assert "github_bounty_bindings" not in agents_sql
        assert "users" in agents_sql
    finally:
        client.close()


def test_posted_bounties_stay_owner_scoped():
    db = _Capture()
    owner = uuid.uuid4()
    assert _run(bounty_service.user_posted_bounties(db, owner)) == []
    assert len(db.stmts) == 1
    sql = _pg(db.stmts[0])
    assert "github_bounty_bindings" not in sql
    assert f"bounties.requester_id = '{owner}'" in sql


def test_solver_setup_excludes_hidden(monkeypatch):
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, exchange_bot_id="acct", exchange_api_key="k")

    def advertised(requester_id, required_ema, title):
        bounty_id = uuid.uuid4()
        binding = SimpleNamespace(bounty_id=bounty_id, repo_full_name="acme/widget", issue_number=1)
        bounty = SimpleNamespace(id=bounty_id, requester_id=requester_id, title=title)
        spec = SimpleNamespace(required_ema=required_ema, amount_ate=100)
        return binding, bounty, spec

    visible = advertised(uuid.uuid4(), 0.2, "Visible title")
    hidden_easy = advertised(uuid.uuid4(), 0.2, "Hidden title")
    hidden_hard = advertised(uuid.uuid4(), 50, "Hidden hard")
    hidden_owned = advertised(user_id, 0, "Hidden mine")

    async def fetch_identity(_db, uid):
        assert uid == user_id
        return SimpleNamespace(login="octo")

    async def fetch_bounties(_db, *, language, limit):
        assert language is None
        assert limit == 100
        return [visible, hidden_easy, hidden_hard, hidden_owned]

    class _Db:
        def __init__(self):
            self.stmts = []

        async def execute(self, stmt):
            self.stmts.append(stmt)
            sql = str(stmt)
            if "github_bounty_bindings" in sql:
                hidden = [hidden_easy[1].id, hidden_hard[1].id, hidden_owned[1].id]
                return _IdRows(hidden)
            if "funder_user_id" in sql:
                return _First(None)
            raise AssertionError(sql)

    monkeypatch.setattr(github_leases, "fetch_identity_for_user", fetch_identity)
    monkeypatch.setattr(github_leases, "fetch_advertised_bounties", fetch_bounties)
    monkeypatch.setattr(solver_setup.exchange, "get_balance", lambda _user: {"reputation": 1})
    monkeypatch.setattr(solver_setup.settings, "TRUST_GATEWAY_TOKEN_URL", "")
    db = _Db()
    body = _run(solver_setup.setup_status(db, user))
    assert [item["title"] for item in body["eligible_bounties"]] == ["Visible title"]
    assert body["ineligible_count"] == 0
    assert any("github_bounty_bindings" in str(stmt) for stmt in db.stmts)


def test_training_card_404_for_hidden_bounty(monkeypatch):
    from fastapi import HTTPException

    from app.routes import training

    run = SimpleNamespace(public=True, bounty_id=uuid.uuid4())

    async def get_run(_db, _run_id):
        return run

    async def hidden(_db, ids):
        return set(ids)

    monkeypatch.setattr(training.training_service, "get_run", get_run)
    monkeypatch.setattr(training, "hidden_bounty_ids", hidden)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(training.get_training_card_html(uuid.uuid4(), db=None))
    assert exc.value.status_code == 404
