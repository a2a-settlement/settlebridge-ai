"""Private and internal repos can advertise. No network; GitHub is faked."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from types import SimpleNamespace

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.cli import github_visibility
from app.models.bounty import BountyStatus
from app.models.github import (
    AcceptanceSpec,
    BindingState,
    FunderLedgerEntry,
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubJob,
    GitHubLease,
    GitHubRepoLink,
    JobKind,
    LedgerStatus,
)
from app.models.user import User, UserType
from app.services import github_app, github_bounty_service

REPO = "acme/widget"
INSTALLATION = 10
BODY_MARKER = "SECRET_ISSUE_BODY_SHOULD_NOT_PRINT"


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

    def add(self, obj):
        self.sync.add(obj)

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


def _engine(*models):
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    for model in models:
        model.__table__.create(engine)
    return engine


def _run(coro):
    return asyncio.run(coro)


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="pat@example.com",
        password_hash="super-secret-password",
        display_name="Funder",
        user_type=UserType.REQUESTER,
        exchange_bot_id="bot-1",
    )


def _job(action: str, repository: dict) -> GitHubJob:
    return GitHubJob(
        kind=JobKind.REPOSITORY.value,
        payload={
            "event": "repository",
            "action": action,
            "data": {
                "action": action,
                "repository": {"full_name": REPO, **repository},
                "installation": {"id": INSTALLATION},
            },
        },
    )


def test_repository_visibility_webhook_does_not_cancel_bindings():
    engine = _engine(GitHubInstallation, GitHubRepoLink, GitHubBountyBinding)
    session = Session(engine, expire_on_commit=False)
    advertised = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=uuid.uuid4(),
        repo_full_name=REPO,
        issue_number=3,
        state=BindingState.ADVERTISED.value,
    )
    cancelled = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=uuid.uuid4(),
        repo_full_name=REPO,
        issue_number=4,
        state=BindingState.CANCELLED.value,
    )
    link = GitHubRepoLink(
        id=uuid.uuid4(),
        installation_id=INSTALLATION,
        repo_full_name=REPO,
        enabled=True,
        default_policy={},
        required_checks=[],
        visibility="public",
    )
    session.add_all(
        [
            GitHubInstallation(
                id=uuid.uuid4(), installation_id=INSTALLATION, account_login="acme"
            ),
            link,
            advertised,
            cancelled,
        ]
    )
    session.commit()
    db = _Bridge(session)
    _run(github_bounty_service.handle_job(db, _job("privatized", {"visibility": "private"})))
    assert link.visibility == "private"
    assert advertised.state == BindingState.ADVERTISED.value
    assert cancelled.state == BindingState.CANCELLED.value

    _run(github_bounty_service.handle_job(db, _job("publicized", {"visibility": "public"})))
    assert link.visibility == "public"
    assert advertised.state == BindingState.ADVERTISED.value
    assert cancelled.state == BindingState.CANCELLED.value

    _run(github_bounty_service.handle_job(db, _job("privatized", {"visibility": "internal"})))
    assert link.visibility == "internal"

    _run(github_bounty_service.handle_job(db, _job("privatized", {})))
    assert link.visibility == "internal"

    _run(github_bounty_service.handle_job(db, _job("publicized", {"visibility": "private"})))
    assert link.visibility == "public"
    assert advertised.state == BindingState.ADVERTISED.value
    session.close()
    engine.dispose()


def _readvertise_engine():
    return _engine(
        User,
        GitHubInstallation,
        GitHubRepoLink,
        GitHubBountyBinding,
        FunderLedgerEntry,
        GitHubLease,
        AcceptanceSpec,
    )


def _seed_cancelled(engine) -> dict:
    user = _user()
    bounty_id = uuid.uuid4()
    settled_id = uuid.uuid4()
    binding = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=bounty_id,
        repo_full_name=REPO,
        issue_number=7,
        state=BindingState.CANCELLED.value,
    )
    settled = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=settled_id,
        repo_full_name=REPO,
        issue_number=8,
        state=BindingState.CANCELLED.value,
    )
    bounties = {
        bounty_id: SimpleNamespace(
            status=BountyStatus.CANCELLED, reward_amount=2500, min_reputation=0.75
        ),
        settled_id: SimpleNamespace(
            status=BountyStatus.COMPLETED, reward_amount=1000, min_reputation=0.75
        ),
    }
    session = Session(engine, expire_on_commit=False)
    session.add_all(
        [
            user,
            GitHubInstallation(
                id=uuid.uuid4(), installation_id=INSTALLATION, account_login="acme"
            ),
            GitHubRepoLink(
                id=uuid.uuid4(),
                installation_id=INSTALLATION,
                repo_full_name=REPO,
                funder_user_id=user.id,
                enabled=True,
                default_policy={},
                required_checks=[],
                visibility="private",
            ),
            binding,
            settled,
            FunderLedgerEntry(
                id=uuid.uuid4(),
                funder_user_id=user.id,
                binding_id=binding.id,
                amount_ate=2500,
                status=LedgerStatus.RELEASED.value,
            ),
        ]
    )
    session.commit()
    session.close()
    return bounties


def _issues() -> list[dict]:
    def issue(number: int, usd: int) -> dict:
        return {
            "number": number,
            "title": f"Issue {number}",
            "body": f"Please reproduce\n```\npytest -q\n```\n{BODY_MARKER}\n",
            "state": "open",
            "labels": [{"name": f"bounty: ${usd}"}],
        }

    return [issue(8, 10), issue(7, 25)]


def _patch(monkeypatch, engine, bounties, *, issues=None, calls=None):
    comments: list = []
    listed = calls if calls is not None else []

    async def get_bounty(_db, bounty_id):
        return bounties[bounty_id]

    async def open_issues(installation_id, repo):
        listed.append((installation_id, repo))
        return [] if issues is None else issues

    async def upsert(*args, **kwargs):
        comments.append(args)
        return 11

    def factory():
        return _Bridge(Session(engine, expire_on_commit=False))

    monkeypatch.setattr(github_bounty_service, "get_bounty", get_bounty)
    monkeypatch.setattr(github_visibility, "_open_issues", open_issues)
    monkeypatch.setattr(github_app, "upsert_sticky_comment", upsert)
    monkeypatch.setattr(
        github_bounty_service.exchange,
        "get_balance_dashboard",
        lambda account_id: {"available": 1_000_000},
    )
    monkeypatch.setattr(github_visibility, "async_session", factory)
    return comments, listed


def _states(engine) -> dict[int, str]:
    session = Session(engine, expire_on_commit=False)
    try:
        rows = session.execute(select(GitHubBountyBinding)).scalars().all()
        return {row.issue_number: row.state for row in rows}
    finally:
        session.close()


def _status_payload(out: str) -> tuple[dict, str]:
    body, status = out.rstrip("\n").rsplit("\n", 1)
    return json.loads(body), status


def test_readvertise_dry_run_reports_would_revive_and_writes_nothing(engine, monkeypatch, capsys):
    bounties = _seed_cancelled(engine)
    comments, listed = _patch(monkeypatch, engine, bounties, issues=_issues())
    code = github_visibility.main(["readvertise", "--repo", REPO, "--dry-run"])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload, status = _status_payload(out)
    assert status == "DRY RUN"
    assert payload["repos"] == [REPO]
    assert payload["issue_numbers"] == [7, 8]
    assert payload["would_revive"] == [7]
    assert payload["would_create"] == []
    assert payload["skipped"] == [8]
    assert BODY_MARKER not in out
    assert "pytest -q" not in out
    assert comments == []
    assert listed == [(INSTALLATION, REPO)]
    assert _states(engine) == {7: BindingState.CANCELLED.value, 8: BindingState.CANCELLED.value}
    assert bounties[_binding_bounty(engine, 7)].status == BountyStatus.CANCELLED


def test_readvertise_revives_a_cancelled_binding(engine, monkeypatch, capsys):
    bounties = _seed_cancelled(engine)
    comments, _listed = _patch(monkeypatch, engine, bounties, issues=_issues())
    code = github_visibility.main(["readvertise", "--repo", REPO])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload, status = _status_payload(out)
    assert status == "APPLIED"
    assert payload["would_revive"] == [7]
    assert payload["skipped"] == [8]
    assert BODY_MARKER not in out
    assert len(comments) == 1
    assert _states(engine) == {7: BindingState.ADVERTISED.value, 8: BindingState.CANCELLED.value}
    revived = bounties[_binding_bounty(engine, 7)]
    assert revived.status == BountyStatus.OPEN
    assert bounties[_binding_bounty(engine, 8)].status == BountyStatus.COMPLETED
    session = Session(engine, expire_on_commit=False)
    try:
        assert session.execute(select(GitHubBountyBinding)).scalars().all().__len__() == 2
        ledger = session.execute(select(FunderLedgerEntry)).scalar_one()
        assert ledger.status == LedgerStatus.ADVERTISED.value
    finally:
        session.close()


def test_readvertise_unknown_visibility_writes_nothing(engine, monkeypatch, capsys):
    bounties = _seed_cancelled(engine)
    session = Session(engine, expire_on_commit=False)
    link = session.execute(select(GitHubRepoLink)).scalar_one()
    link.visibility = None
    session.commit()
    session.close()
    comments, listed = _patch(monkeypatch, engine, bounties, issues=_issues())
    code = github_visibility.main(["readvertise", "--repo", REPO])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert err.strip() == "visibility_unknown"
    assert comments == []
    assert listed == []
    assert _states(engine)[7] == BindingState.CANCELLED.value


def _binding_bounty(engine, issue_number: int):
    session = Session(engine, expire_on_commit=False)
    try:
        row = session.execute(
            select(GitHubBountyBinding).where(GitHubBountyBinding.issue_number == issue_number)
        ).scalar_one()
        return row.bounty_id
    finally:
        session.close()


@pytest.fixture()
def engine():
    eng = _readvertise_engine()
    try:
        yield eng
    finally:
        eng.dispose()
