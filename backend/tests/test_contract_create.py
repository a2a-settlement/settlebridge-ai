"""POST /api/contracts checks the named agent and stores that agent's bot id.

In-memory SQLite. No network.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.contract import ServiceContract
from app.models.notification import Notification, NotificationType
from app.models.user import User, UserType
from app.routes import contracts as routes

_OMIT = object()
AGENT_BOT = "agent-bot"


class Bridge:
    """Async session facade over a sync SQLite session."""

    def __init__(self, sync: Session):
        self.sync = sync
        self.commits = 0

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()
        self.commits += 1

    async def refresh(self, instance, attribute_names=None, with_for_update=None):
        self.sync.refresh(
            instance,
            attribute_names=attribute_names,
            with_for_update=with_for_update,
        )


def _engine():
    import app.models as registered

    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for model in (registered.User, registered.ServiceContract, registered.Notification):
        model.__table__.create(eng)
    return eng


@pytest.fixture()
def db():
    eng = _engine()
    bridge = Bridge(Session(eng, expire_on_commit=False))
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


def _user(
    email: str,
    user_type: UserType = UserType.AGENT_OPERATOR,
    bot_id: str | None = AGENT_BOT,
) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        display_name=email.split("@", 1)[0],
        user_type=user_type,
        exchange_bot_id=bot_id,
    )


def _add(db: Bridge, user: User) -> User:
    db.sync.add(user)
    db.sync.flush()
    return user


def _payload(agent_id: uuid.UUID, bot_id: object = _OMIT) -> dict:
    body: dict = {
        "title": "Weekly digest",
        "description": "Send the digest",
        "agent_user_id": str(agent_id),
        "reward_per_snapshot": 100,
        "schedule": "0 9 * * 1",
        "schedule_description": "Mondays at 9",
    }
    if bot_id is not _OMIT:
        body["agent_exchange_bot_id"] = bot_id
    return body


def _post(db: Bridge, requester: User, payload: dict):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/contracts")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: requester
    with TestClient(app) as client:
        return client.post("/api/contracts", json=payload)


def _count(db: Bridge, model) -> int:
    return int(db.sync.scalar(select(func.count()).select_from(model)) or 0)


def _assert_rejected(response, db: Bridge, detail: str) -> None:
    assert response.status_code == 400
    assert response.json()["detail"] == detail
    assert _count(db, ServiceContract) == 0
    assert _count(db, Notification) == 0
    assert db.commits == 0


@pytest.mark.parametrize(
    ("user_type", "supplied"),
    [
        (UserType.AGENT_OPERATOR, _OMIT),
        (UserType.AGENT_OPERATOR, None),
        (UserType.BOTH, AGENT_BOT),
    ],
)
def test_create_stores_the_agents_exchange_bot(db, user_type, supplied):
    requester = _add(db, _user("requester@example.com", UserType.REQUESTER, "requester-bot"))
    agent = _add(db, _user("agent@example.com", user_type, AGENT_BOT))

    response = _post(db, requester, _payload(agent.id, supplied))

    assert response.status_code == 201
    body = response.json()
    assert body["agent_user_id"] == str(agent.id)
    assert body["agent_exchange_bot_id"] == AGENT_BOT
    assert body["requester_id"] == str(requester.id)
    saved = db.sync.scalars(select(ServiceContract)).one()
    assert saved.agent_exchange_bot_id == AGENT_BOT
    assert saved.agent_user_id == agent.id
    assert saved.requester_id == requester.id
    note = db.sync.scalars(select(Notification)).one()
    assert note.user_id == agent.id
    assert note.type == NotificationType.CONTRACT_CREATED
    assert note.reference_id == saved.id
    assert db.commits == 1


def test_unknown_agent_is_rejected_before_create(db):
    requester = _add(db, _user("requester@example.com", UserType.REQUESTER, "requester-bot"))
    response = _post(db, requester, _payload(uuid.uuid4(), "invented-bot"))
    _assert_rejected(response, db, "Agent user not found")


def test_cannot_contract_yourself(db):
    operator = _add(db, _user("agent@example.com", UserType.AGENT_OPERATOR, AGENT_BOT))
    response = _post(db, operator, _payload(operator.id, AGENT_BOT))
    _assert_rejected(response, db, "Cannot create a contract with yourself")


def test_requester_is_not_an_agent(db):
    requester = _add(db, _user("requester@example.com", UserType.REQUESTER, "requester-bot"))
    other = _add(db, _user("other@example.com", UserType.REQUESTER, "other-bot"))
    response = _post(db, requester, _payload(other.id, "other-bot"))
    _assert_rejected(response, db, "Agent user type must be agent_operator or both")


@pytest.mark.parametrize("bot_id", [None, ""])
def test_agent_without_a_bot_is_rejected(db, bot_id):
    requester = _add(db, _user("requester@example.com", UserType.REQUESTER, "requester-bot"))
    agent = _add(db, _user("agent@example.com", UserType.AGENT_OPERATOR, bot_id))
    response = _post(db, requester, _payload(agent.id, "caller-supplied"))
    _assert_rejected(response, db, "Agent has no exchange bot")


@pytest.mark.parametrize("supplied", ["other-bot", ""])
def test_supplied_bot_id_must_match_the_agent(db, supplied):
    requester = _add(db, _user("requester@example.com", UserType.REQUESTER, "requester-bot"))
    agent = _add(db, _user("agent@example.com", UserType.BOTH, AGENT_BOT))
    response = _post(db, requester, _payload(agent.id, supplied))
    _assert_rejected(response, db, "agent_exchange_bot_id does not match the agent's exchange bot")
