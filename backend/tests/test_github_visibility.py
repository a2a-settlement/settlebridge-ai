"""Visibility backfill. In-memory SQLite; GitHub is faked. The Bounty table is not created."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.cli import github_visibility
from app.models.bounty import BountyStatus
from app.models.github import (
    BindingState,
    FunderLedgerEntry,
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubLease,
    GitHubRepoLink,
    LedgerStatus,
)
from app.models.user import User, UserType
from app.services import github_app, github_bounty_service

TOKEN = "ghs_visibility_secret"
PUBLIC = "acme/public"
PRIVATE = "acme/private"
INTERNAL = "acme/internal"
INSTALLATION = 10


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.sync.close()
        return False

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.commits += 1
        self.sync.commit()

    async def rollback(self):
        self.rollbacks += 1
        self.sync.rollback()


def _engine():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    for model in (
        User,
        GitHubInstallation,
        GitHubRepoLink,
        GitHubBountyBinding,
        FunderLedgerEntry,
        GitHubLease,
    ):
        model.__table__.create(engine)
    return engine


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="pat@example.com",
        password_hash="super-secret-password",
        display_name="Funder",
        user_type=UserType.REQUESTER,
    )


def _binding(repo: str, issue: int, bounties: dict, *, state: str) -> GitHubBountyBinding:
    bounty_id = uuid.uuid4()
    bounties[bounty_id] = SimpleNamespace(status=BountyStatus.OPEN)
    return GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=bounty_id,
        repo_full_name=repo,
        issue_number=issue,
        state=state,
    )


def _ledger(binding: GitHubBountyBinding, user_id) -> FunderLedgerEntry:
    return FunderLedgerEntry(
        id=uuid.uuid4(),
        funder_user_id=user_id,
        binding_id=binding.id,
        amount_ate=2500,
        status=LedgerStatus.ADVERTISED.value,
    )


def _lease(binding: GitHubBountyBinding, user_id) -> GitHubLease:
    now = datetime.now(timezone.utc)
    return GitHubLease(
        id=uuid.uuid4(),
        binding_id=binding.id,
        agent_id="solver",
        user_id=user_id,
        github_user_id=7,
        spec_id=uuid.uuid4(),
        token_hash="a" * 64,
        status="work",
        work_expires_at=now + timedelta(hours=1),
        hard_expires_at=now + timedelta(days=1),
    )


def _seed(engine) -> dict:
    bounties: dict = {}
    user = _user()
    installation = GitHubInstallation(
        id=uuid.uuid4(), installation_id=INSTALLATION, account_login="acme"
    )
    links = [
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=INSTALLATION,
            repo_full_name=name,
            enabled=True,
            default_policy={},
            required_checks=[],
            visibility=None,
        )
        for name in (INTERNAL, PRIVATE, PUBLIC)
    ]
    private_open = _binding(PRIVATE, 1, bounties, state=BindingState.ADVERTISED.value)
    private_leased = _binding(PRIVATE, 2, bounties, state=BindingState.ADVERTISED.value)
    private_settled = _binding(
        PRIVATE, 3, bounties, state=BindingState.SETTLEMENT_PENDING.value
    )
    internal_open = _binding(INTERNAL, 4, bounties, state=BindingState.EVALUATING.value)
    public_open = _binding(PUBLIC, 5, bounties, state=BindingState.ADVERTISED.value)
    bindings = [private_open, private_leased, private_settled, internal_open, public_open]
    ledgers = [
        _ledger(binding, user.id)
        for binding in (private_open, private_leased, internal_open, public_open)
    ]
    session = Session(engine, expire_on_commit=False)
    try:
        session.add_all(
            [user, installation, *links, *bindings, *ledgers, _lease(private_leased, user.id)]
        )
        session.commit()
    finally:
        session.close()
    return bounties


def _sessions(monkeypatch, engine) -> list[_Bridge]:
    opened: list[_Bridge] = []

    def factory():
        bridge = _Bridge(Session(engine, expire_on_commit=False))
        opened.append(bridge)
        return bridge

    monkeypatch.setattr(github_visibility, "async_session", factory)
    return opened


def _payloads():
    return {
        PUBLIC: {"visibility": "public", "private": False, "token": TOKEN},
        PRIVATE: {"visibility": "private", "private": True, "token": TOKEN},
        INTERNAL: {"visibility": "internal", "private": True, "token": TOKEN},
    }


def _patch_github(monkeypatch, bounties: dict, payloads: dict | None = None, error: str | None = None):
    comments: list[tuple] = []
    found = payloads if payloads is not None else _payloads()

    async def get_repository(installation_id, repo):
        if error is not None:
            raise github_app.GitHubAPIError(f"{error} {TOKEN}", 503)
        assert installation_id == INSTALLATION
        return found[repo]

    async def upsert(*args, **kwargs):
        comments.append(args)
        return 9

    async def get_bounty(_db, bounty_id):
        return bounties[bounty_id]

    monkeypatch.setattr(github_app, "get_repository", get_repository)
    monkeypatch.setattr(github_app, "upsert_sticky_comment", upsert)
    monkeypatch.setattr(github_bounty_service, "get_bounty", get_bounty)
    return comments


def _stored(engine) -> dict:
    session = Session(engine, expire_on_commit=False)
    try:
        links = {
            row.repo_full_name: row.visibility
            for row in session.execute(select(GitHubRepoLink)).scalars()
        }
        bindings = {
            (row.repo_full_name, row.issue_number): row.state
            for row in session.execute(select(GitHubBountyBinding)).scalars()
        }
        ledgers = {
            row.binding_id: row.status
            for row in session.execute(select(FunderLedgerEntry)).scalars()
        }
        leases = [row.status for row in session.execute(select(GitHubLease)).scalars()]
        return {"links": links, "bindings": bindings, "ledgers": ledgers, "leases": leases}
    finally:
        session.close()


def _status_payload(out: str) -> tuple[dict, str]:
    body, status = out.rstrip("\n").rsplit("\n", 1)
    return json.loads(body), status


@pytest.fixture()
def engine():
    eng = _engine()
    try:
        yield eng
    finally:
        eng.dispose()


def test_backfill_dry_run_changes_nothing(engine, monkeypatch, capsys):
    bounties = _seed(engine)
    comments = _patch_github(monkeypatch, bounties)
    opened = _sessions(monkeypatch, engine)
    code = github_visibility.main(["backfill", "--dry-run"])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload, status = _status_payload(out)
    assert status == "DRY RUN"
    assert payload == {
        "active_lease": 1,
        "cancelled": 2,
        "internal": 1,
        "private": 1,
        "public": 1,
        "repos": [INTERNAL, PRIVATE, PUBLIC],
        "unknown": 0,
    }
    assert TOKEN not in out
    assert comments == []
    assert opened[0].commits == 0
    assert opened[0].rollbacks == 1
    stored = _stored(engine)
    assert stored["links"] == {PUBLIC: None, PRIVATE: None, INTERNAL: None}
    assert stored["bindings"][(PRIVATE, 1)] == BindingState.ADVERTISED.value
    assert stored["bindings"][(INTERNAL, 4)] == BindingState.EVALUATING.value
    assert set(stored["ledgers"].values()) == {LedgerStatus.ADVERTISED.value}
    assert stored["leases"] == ["work"]
    assert all(bounty.status == BountyStatus.OPEN for bounty in bounties.values())


def test_backfill_stores_visibility_and_cancels_unleased_bindings(engine, monkeypatch, capsys):
    bounties = _seed(engine)
    comments = _patch_github(monkeypatch, bounties)
    opened = _sessions(monkeypatch, engine)
    code = github_visibility.main(["backfill"])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload, status = _status_payload(out)
    assert status == "APPLIED"
    assert payload["cancelled"] == 2
    assert payload["active_lease"] == 1
    assert payload["repos"] == [INTERNAL, PRIVATE, PUBLIC]
    assert TOKEN not in out + err
    assert opened[0].commits == 1
    assert len(comments) == 2
    assert sorted(args[1] for args in comments) == [INTERNAL, PRIVATE]
    assert all("not public" in args[4] for args in comments)
    stored = _stored(engine)
    assert stored["links"] == {PUBLIC: "public", PRIVATE: "private", INTERNAL: "internal"}
    assert stored["bindings"][(PRIVATE, 1)] == BindingState.CANCELLED.value
    assert stored["bindings"][(PRIVATE, 2)] == BindingState.ADVERTISED.value
    assert stored["bindings"][(PRIVATE, 3)] == BindingState.SETTLEMENT_PENDING.value
    assert stored["bindings"][(INTERNAL, 4)] == BindingState.CANCELLED.value
    assert stored["bindings"][(PUBLIC, 5)] == BindingState.ADVERTISED.value
    assert stored["leases"] == ["work"]
    statuses = [bounty.status for bounty in bounties.values()]
    assert statuses.count(BountyStatus.CANCELLED) == 2
    assert statuses.count(BountyStatus.OPEN) == 3


def test_backfill_github_failure_prints_no_token(engine, monkeypatch, capsys):
    bounties = _seed(engine)
    _patch_github(monkeypatch, bounties, error="down")
    opened = _sessions(monkeypatch, engine)
    code = github_visibility.main(["backfill"])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert "GitHub read failed for" in err
    assert TOKEN not in err
    assert opened[0].commits == 0
    assert opened[0].rollbacks == 1
    assert _stored(engine)["links"] == {PUBLIC: None, PRIVATE: None, INTERNAL: None}
