"""Score-history reads stay inside the caller's runs, or public non-hidden ones.

In-memory SQLite. Bounties are a slim table because the Bounty model uses
Postgres ARRAY and Enum columns. GitHub bindings use the real models so
hidden_bounty_ids matches the public training card.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubBountyBinding, GitHubInstallation, GitHubRepoLink
from app.models.score_history import ScoreHistory, ScoreMode
from app.models.training_run import TrainingRun, TrainingRunStatus
from app.models.user import User, UserType
from app.routes.training import router

URL = "/api/score-history"
NOT_FOUND = {"detail": "Training run not found"}
_NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)

SECRET_PRIVATE = "SECRET_PRIVATE_REASON"
SECRET_PRIVATE_PROD = "SECRET_PRIVATE_PROD_REASON"
SECRET_HIDDEN = "SECRET_HIDDEN_REASON"
SECRET_UNPUBLISHED = "SECRET_UNPUBLISHED_REASON"
SECRET_NORUN = "SECRET_NORUN_REASON"
SECRET_OWN = "SECRET_OWN_REASON"
SECRET_OWN_NORUN = "SECRET_OWN_NORUN_REASON"
PUBLIC_PLAIN = "PUBLIC_PLAIN_REASON"
PUBLIC_OPEN = "PUBLIC_OPEN_REASON"
PUBLIC_PROD = "PUBLIC_PROD_REASON"


def _at(minute: int) -> datetime:
    return _NOW.replace(minute=minute)


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)


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
        TrainingRun,
        ScoreHistory,
        GitHubInstallation,
        GitHubRepoLink,
        GitHubBountyBinding,
    ):
        model.__table__.create(eng)
    return eng


def _user(user_id: uuid.UUID, email: str, name: str, kind: UserType) -> User:
    return User(
        id=user_id,
        email=email,
        display_name=name,
        user_type=kind,
        created_at=_NOW,
    )


def _bounty(db: _Bridge, bounty_id: uuid.UUID, requester_id: uuid.UUID) -> None:
    db.sync.execute(
        text("INSERT INTO bounties (id, requester_id) VALUES (:id, :rid)"),
        {"id": bounty_id.hex, "rid": requester_id.hex},
    )


def _link(db: _Bridge, repo: str, visibility: str) -> None:
    db.sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=1,
            repo_full_name=repo,
            enabled=True,
            default_policy={},
            required_checks=[],
            visibility=visibility,
        )
    )


def _binding(db: _Bridge, bounty_id: uuid.UUID, repo: str, issue: int) -> None:
    db.sync.add(
        GitHubBountyBinding(
            id=uuid.uuid4(),
            bounty_id=bounty_id,
            repo_full_name=repo,
            issue_number=issue,
            state="advertised",
        )
    )


def _run(
    db: _Bridge,
    *,
    agent: uuid.UUID,
    bounty: uuid.UUID,
    public: bool,
    when: datetime,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    db.sync.add(
        TrainingRun(
            id=run_id,
            bounty_id=bounty,
            agent_user_id=agent,
            status=TrainingRunStatus.RUNNING,
            max_iterations=3,
            stake_budget=100,
            stake_spent=0,
            score_threshold=0.8,
            task_type="demo",
            iterations_completed=1,
            created_at=when,
            public=public,
        )
    )
    return run_id


def _score(
    db: _Bridge,
    *,
    agent: uuid.UUID,
    bounty: uuid.UUID,
    run: uuid.UUID | None,
    reasoning: str,
    when: datetime,
    mode: ScoreMode = ScoreMode.TRAINING,
    task: str = "demo",
) -> None:
    db.sync.add(
        ScoreHistory(
            id=uuid.uuid4(),
            agent_user_id=agent,
            bounty_id=bounty,
            training_run_id=run,
            task_type=task,
            numeric_score=0.42,
            reasoning=reasoning,
            diagnostics={"review": reasoning},
            mode=mode,
            created_at=when,
        )
    )


def _seed(db: _Bridge) -> dict:
    caller, agent, requester, admin = (uuid.uuid4() for _ in range(4))
    plain, hidden, opened, own_bounty = (uuid.uuid4() for _ in range(4))
    db.sync.add_all(
        [
            _user(caller, "caller@example.com", "Caller", UserType.AGENT_OPERATOR),
            _user(agent, "agent@example.com", "Agent", UserType.AGENT_OPERATOR),
            _user(requester, "requester@example.com", "Requester", UserType.REQUESTER),
            _user(admin, "admin@example.com", "Admin", UserType.BOTH),
            GitHubInstallation(id=uuid.uuid4(), installation_id=1, account_login="acme"),
        ]
    )
    db.sync.flush()
    for bounty_id in (plain, hidden, opened):
        _bounty(db, bounty_id, requester)
    _bounty(db, own_bounty, caller)
    _link(db, "acme/private", "private")
    _link(db, "acme/public", "public")
    _binding(db, hidden, "acme/private", 1)
    _binding(db, opened, "acme/public", 1)

    private = _run(db, agent=agent, bounty=plain, public=False, when=_at(1))
    public_plain = _run(db, agent=agent, bounty=plain, public=True, when=_at(2))
    hidden_run = _run(db, agent=agent, bounty=hidden, public=True, when=_at(3))
    unpublished = _run(db, agent=agent, bounty=opened, public=False, when=_at(4))
    public_open = _run(db, agent=agent, bounty=opened, public=True, when=_at(5))
    own = _run(db, agent=caller, bounty=own_bounty, public=False, when=_at(8))

    _score(db, agent=agent, bounty=plain, run=private, reasoning=SECRET_PRIVATE, when=_at(1))
    _score(
        db,
        agent=agent,
        bounty=plain,
        run=private,
        reasoning=SECRET_PRIVATE_PROD,
        when=_at(10),
        mode=ScoreMode.PRODUCTION,
    )
    _score(db, agent=agent, bounty=plain, run=public_plain, reasoning=PUBLIC_PLAIN, when=_at(2))
    _score(db, agent=agent, bounty=hidden, run=hidden_run, reasoning=SECRET_HIDDEN, when=_at(3))
    _score(
        db,
        agent=agent,
        bounty=opened,
        run=unpublished,
        reasoning=SECRET_UNPUBLISHED,
        when=_at(4),
    )
    _score(db, agent=agent, bounty=opened, run=public_open, reasoning=PUBLIC_OPEN, when=_at(5))
    _score(db, agent=agent, bounty=plain, run=None, reasoning=SECRET_NORUN, when=_at(6))
    _score(
        db,
        agent=agent,
        bounty=opened,
        run=public_open,
        reasoning=PUBLIC_PROD,
        when=_at(7),
        mode=ScoreMode.PRODUCTION,
        task="other",
    )
    _score(db, agent=caller, bounty=own_bounty, run=own, reasoning=SECRET_OWN, when=_at(8))
    _score(db, agent=caller, bounty=own_bounty, run=None, reasoning=SECRET_OWN_NORUN, when=_at(9))
    db.sync.commit()
    return {
        "caller": caller,
        "agent": agent,
        "requester": requester,
        "admin": admin,
        "runs": {
            "private": private,
            "public_plain": public_plain,
            "hidden": hidden_run,
            "unpublished": unpublished,
            "public_open": public_open,
            "own": own,
        },
        "agent_rows": [
            SECRET_PRIVATE,
            PUBLIC_PLAIN,
            SECRET_HIDDEN,
            SECRET_UNPUBLISHED,
            PUBLIC_OPEN,
            SECRET_NORUN,
            PUBLIC_PROD,
            SECRET_PRIVATE_PROD,
        ],
        "public_rows": [PUBLIC_PLAIN, PUBLIC_OPEN, PUBLIC_PROD],
        "own_rows": [SECRET_OWN, SECRET_OWN_NORUN],
    }


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", "")
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    seeded = _seed(bridge)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=seeded["caller"])
    client = TestClient(app)
    try:
        yield {"client": client, "app": app, **seeded}
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _as(world, user_id: uuid.UUID) -> None:
    world["app"].dependency_overrides[get_current_user] = (
        lambda user_id=user_id: SimpleNamespace(id=user_id)
    )


def _as_admin(world, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(world["admin"]))
    _as(world, world["admin"])


def _reasons(response) -> list[str]:
    body = response.json()
    assert isinstance(body, list)
    return [row["reasoning"] for row in body]


def _get(world, **params):
    return world["client"].get(URL, params=params)


def test_no_params_returns_only_the_callers_rows(world):
    response = _get(world)
    assert response.status_code == 200
    assert _reasons(response) == world["own_rows"]
    assert SECRET_PRIVATE not in response.text
    assert "SECRET_HIDDEN" not in response.text


def test_own_agent_id_includes_private_and_unlinked_rows(world):
    response = _get(world, agent_id=str(world["caller"]))
    assert response.status_code == 200
    assert _reasons(response) == world["own_rows"]


def test_own_limit_offset_and_task_type_still_apply(world):
    page = _get(world, limit=1, offset=1)
    assert _reasons(page) == [SECRET_OWN_NORUN]
    assert _reasons(_get(world, task_type="missing")) == []
    assert _reasons(_get(world, task_type="demo")) == world["own_rows"]


def test_other_agent_returns_only_public_unhidden_rows(world):
    response = _get(world, agent_id=str(world["agent"]))
    assert response.status_code == 200
    assert _reasons(response) == world["public_rows"]
    assert "SECRET_" not in response.text
    assert response.json()[0]["diagnostics"] == {"review": PUBLIC_PLAIN}


def test_other_agent_pagination_skips_filtered_rows(world):
    agent_id = str(world["agent"])
    assert _reasons(_get(world, agent_id=agent_id, limit=1, offset=0)) == [PUBLIC_PLAIN]
    assert _reasons(_get(world, agent_id=agent_id, limit=1, offset=1)) == [PUBLIC_OPEN]
    assert _reasons(_get(world, agent_id=agent_id, limit=1, offset=2)) == [PUBLIC_PROD]
    assert _reasons(_get(world, agent_id=agent_id, limit=2, offset=1)) == [
        PUBLIC_OPEN,
        PUBLIC_PROD,
    ]


def test_other_agent_mode_and_task_type_still_filter(world):
    agent_id = str(world["agent"])
    assert _reasons(_get(world, agent_id=agent_id, mode="production")) == [PUBLIC_PROD]
    assert _reasons(_get(world, agent_id=agent_id, task_type="other")) == [PUBLIC_PROD]
    assert _reasons(_get(world, agent_id=agent_id, mode="training", limit=1, offset=1)) == [
        PUBLIC_OPEN
    ]


def test_requester_browsing_by_agent_id_does_not_see_private_rows(world):
    _as(world, world["requester"])
    response = _get(world, agent_id=str(world["agent"]))
    assert _reasons(response) == world["public_rows"]
    assert "SECRET_" not in response.text


@pytest.mark.parametrize("key", ["private", "hidden", "unpublished", None])
def test_stranger_run_lookup_is_404(world, key):
    run_id = uuid.uuid4() if key is None else world["runs"][key]
    response = _get(world, training_run_id=str(run_id))
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert "SECRET_" not in response.text


def test_other_agent_plus_private_run_is_404_not_empty(world):
    response = _get(
        world,
        agent_id=str(world["agent"]),
        training_run_id=str(world["runs"]["private"]),
    )
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert "SECRET_" not in response.text


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("public_plain", [PUBLIC_PLAIN]),
        ("public_open", [PUBLIC_OPEN, PUBLIC_PROD]),
    ],
)
def test_public_run_is_visible_by_id(world, key, expected):
    response = _get(world, training_run_id=str(world["runs"][key]))
    assert response.status_code == 200
    assert _reasons(response) == expected
    assert "SECRET_" not in response.text


def test_public_run_keeps_mode_filter(world):
    response = _get(world, training_run_id=str(world["runs"]["public_open"]), mode="training")
    assert _reasons(response) == [PUBLIC_OPEN]


def test_agent_can_read_own_private_and_hidden_runs(world):
    _as(world, world["agent"])
    private = _get(world, training_run_id=str(world["runs"]["private"]))
    hidden = _get(world, training_run_id=str(world["runs"]["hidden"]))
    assert _reasons(private) == [SECRET_PRIVATE, SECRET_PRIVATE_PROD]
    assert _reasons(hidden) == [SECRET_HIDDEN]
    assert _reasons(_get(world)) == world["agent_rows"]


def test_requester_can_read_private_and_hidden_runs(world):
    _as(world, world["requester"])
    private = _get(world, training_run_id=str(world["runs"]["private"]))
    hidden = _get(world, training_run_id=str(world["runs"]["hidden"]))
    unpublished = _get(world, training_run_id=str(world["runs"]["unpublished"]))
    assert private.status_code == 200
    assert _reasons(private) == [SECRET_PRIVATE, SECRET_PRIVATE_PROD]
    assert _reasons(hidden) == [SECRET_HIDDEN]
    assert _reasons(unpublished) == [SECRET_UNPUBLISHED]


def test_requester_run_and_agent_id_still_sees_private_rows(world):
    _as(world, world["requester"])
    response = _get(
        world,
        agent_id=str(world["agent"]),
        training_run_id=str(world["runs"]["private"]),
    )
    assert response.status_code == 200
    assert _reasons(response) == [SECRET_PRIVATE, SECRET_PRIVATE_PROD]


def test_admin_sees_private_rows_and_missing_run_is_empty(world, monkeypatch):
    _as_admin(world, monkeypatch)
    listed = _get(world, agent_id=str(world["agent"]))
    private = _get(world, training_run_id=str(world["runs"]["private"]))
    missing = _get(world, training_run_id=str(uuid.uuid4()))
    untouched = _get(world)
    assert _reasons(listed) == world["agent_rows"]
    assert _reasons(private) == [SECRET_PRIVATE, SECRET_PRIVATE_PROD]
    assert missing.status_code == 200
    assert missing.json() == []
    assert untouched.json() == []


def test_invalid_agent_id_is_400(world):
    response = _get(
        world,
        agent_id="not-a-uuid",
        training_run_id=str(world["runs"]["private"]),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "agent_id must be a valid UUID"
    assert "SECRET_" not in response.text
