"""Verifier world for sb-pr32 (authorization_confidentiality).

Property: a bounty on a private repository is visible only to a signed-in user
whose linked GitHub account has read access on that repository. The requester,
the funder, and the author of a submission may also see that material.

Each public method is one read. The read runs in a forked child that imports
the application from /work, builds an in-memory database, and performs one
HTTP call in-process. The parent registers the private material as a tenant-b
canary and records an authorization capture only when an unentitled actor's
response contains it. ``outcome.leaked`` is true only in that case.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

_CANARY = "tenantbprivatematerial"
_PUBLIC_TEXT = "public description text"
_PRIVATE_REPO = "tenantb/private-app"
_MAX_BODY = 8192
_NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
_READ = frozenset({"read", "triage", "write", "maintain", "admin"})
_AUTHORS = frozenset({"requester", "funder", "submitter"})
_ACTORS = frozenset({"anonymous", "outsider", "reader", "requester", "funder", "submitter"})
_BOUNTIES = frozenset({"public", "private"})
# actor -> (permission, github user id reported for that login)
_ACCESS = {
    "reader": ("read", 2002),
    "outsider": ("none", None),
    "funder": ("admin", 1001),
    "submitter": ("none", None),
    "requester": ("none", None),
}
_GITHUB_IDS = {
    "reader": 2002,
    "outsider": 3003,
    "funder": 1001,
    "submitter": 6006,
}
_USER_KIND = {
    "requester": "requester",
    "funder": "agent",
    "reader": "agent",
    "outsider": "agent",
    "submitter": "agent",
}


def _prepare_env() -> None:
    os.environ["MARKETPLACE_ENABLED"] = "true"
    os.environ["GATEWAY_ENABLED"] = "false"
    os.environ["DEBUG"] = "false"
    os.environ["JWT_SECRET"] = "sbc-sb-pr32"
    os.environ["SECRET_KEY"] = "sbc-sb-pr32"
    os.environ["GITHUB_SETTLEMENT_ENABLED"] = "false"
    os.environ["GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM"] = "false"


def _require_actor(actor: object) -> str:
    text = require_text(actor, "actor", 32)
    if text not in _ACTORS:
        raise ValueError(f"actor must be one of {sorted(_ACTORS)}")
    return text


def _require_bounty(bounty: object) -> str:
    text = require_text(bounty, "bounty", 16)
    if text not in _BOUNTIES:
        raise ValueError(f"bounty must be one of {sorted(_BOUNTIES)}")
    return text


def _identity(actor: str) -> str:
    """Tenant of ``actor`` under the property, independent of the library."""
    if actor in _AUTHORS:
        return "tenant-b"
    permission, github_id = _ACCESS.get(actor, ("none", None))
    linked = _GITHUB_IDS.get(actor)
    if permission in _READ and isinstance(github_id, int) and github_id == linked:
        return "tenant-b"
    return "tenant-a"


def _clip(text: str) -> str:
    if len(text) <= _MAX_BODY:
        return text
    at = text.find(_CANARY)
    if at < 0:
        return text[:_MAX_BODY]
    start = max(0, at - 512)
    return text[start : start + _MAX_BODY]


def _enable_sqlite_types() -> None:
    from sqlalchemy.dialects.postgresql import ARRAY, JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb_sqlite(type_, compiler, **kwargs):
        del type_, compiler, kwargs
        return "JSON"

    @compiles(ARRAY, "sqlite")
    def _array_sqlite(type_, compiler, **kwargs):
        del type_, compiler, kwargs
        return "JSON"


class _Bridge:
    """Async session surface over one synchronous in-memory session."""

    def __init__(self, sync):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    async def scalar(self, statement, *args, **kwargs):
        return self.sync.scalar(statement, *args, **kwargs)

    async def scalars(self, statement, *args, **kwargs):
        return self.sync.scalars(statement, *args, **kwargs)

    async def get(self, model, ident, **kwargs):
        return self.sync.get(model, ident)

    def add(self, obj):
        self.sync.add(obj)

    def add_all(self, objs):
        self.sync.add_all(objs)

    async def delete(self, obj):
        self.sync.delete(obj)

    async def flush(self, *args, **kwargs):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()

    async def refresh(self, obj, *args, **kwargs):
        self.sync.refresh(obj)

    async def close(self):
        return None

    def begin_nested(self):
        return _Nested(self.sync.begin_nested())


class _Nested:
    def __init__(self, transaction):
        self._transaction = transaction

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc, tb
        if exc_type is None:
            self._transaction.commit()
        else:
            self._transaction.rollback()
        return False


def _seed(sync):
    from app.models.bounty import Bounty, BountyMode, BountyStatus, Difficulty, ProvenanceTier
    from app.models.claim import Claim, ClaimStatus
    from app.models.github import (
        GitHubBountyBinding,
        GitHubIdentity,
        GitHubInstallation,
        GitHubRepoLink,
    )
    from app.models.submission import Submission, SubmissionStatus
    from app.models.user import User, UserType

    kinds = {
        "requester": UserType.REQUESTER,
        "agent": UserType.AGENT_OPERATOR,
    }
    users = {}
    for name, kind in _USER_KIND.items():
        user = User(
            id=uuid.uuid4(),
            email=f"{name}@example.test",
            display_name=name,
            user_type=kinds[kind],
            exchange_bot_id=f"{name}-agent",
            created_at=_NOW,
            updated_at=_NOW,
        )
        users[name] = user
        sync.add(user)
    sync.flush()
    sync.add(
        GitHubInstallation(
            id=uuid.uuid4(),
            installation_id=1,
            account_login="tenantb",
            created_at=_NOW,
        )
    )
    for name, github_id in _GITHUB_IDS.items():
        sync.add(
            GitHubIdentity(
                id=uuid.uuid4(),
                user_id=users[name].id,
                github_user_id=github_id,
                login=name,
                verified_at=_NOW,
                created_at=_NOW,
            )
        )
    sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=1,
            repo_full_name=_PRIVATE_REPO,
            funder_user_id=users["funder"].id,
            enabled=True,
            default_policy={},
            required_checks=[],
            visibility="private",
            created_at=_NOW,
        )
    )
    public = Bounty(
        id=uuid.uuid4(),
        requester_id=users["requester"].id,
        title="Public bounty",
        description=_PUBLIC_TEXT,
        reward_amount=10,
        status=BountyStatus.OPEN,
        difficulty=Difficulty.MEDIUM,
        provenance_tier=ProvenanceTier.TIER1_SELF_DECLARED,
        max_claims=1,
        auto_approve=False,
        created_at=_NOW,
        updated_at=_NOW,
        mode=BountyMode.PRODUCTION,
    )
    private = Bounty(
        id=uuid.uuid4(),
        requester_id=users["requester"].id,
        title="Private bounty",
        description=_CANARY,
        reward_amount=20,
        status=BountyStatus.OPEN,
        difficulty=Difficulty.MEDIUM,
        provenance_tier=ProvenanceTier.TIER1_SELF_DECLARED,
        max_claims=1,
        auto_approve=False,
        created_at=_NOW,
        updated_at=_NOW,
        mode=BountyMode.PRODUCTION,
    )
    sync.add_all([public, private])
    sync.flush()
    sync.add(
        GitHubBountyBinding(
            id=uuid.uuid4(),
            bounty_id=private.id,
            repo_full_name=_PRIVATE_REPO,
            issue_number=7,
            state="advertised",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    claim = Claim(
        id=uuid.uuid4(),
        bounty_id=private.id,
        agent_user_id=users["submitter"].id,
        agent_exchange_bot_id=users["submitter"].exchange_bot_id,
        status=ClaimStatus.SUBMITTED,
        claimed_at=_NOW,
    )
    sync.add(claim)
    sync.flush()
    submission = Submission(
        id=uuid.uuid4(),
        claim_id=claim.id,
        bounty_id=private.id,
        agent_user_id=users["submitter"].id,
        deliverable={"content": _CANARY, "content_type": "text/plain"},
        status=SubmissionStatus.APPROVED,
        public_share=False,
        submitted_at=_NOW,
        score=90,
    )
    sync.add(submission)
    sync.commit()
    return users, {
        "public": str(public.id),
        "private": str(private.id),
        "submission": str(submission.id),
    }


def _patch_github() -> None:
    from app.services import github_app

    async def get_collaborator_record(installation_id, repo, username):
        del installation_id
        if repo != _PRIVATE_REPO:
            return ("none", None)
        permission, github_id = _ACCESS.get(username, ("none", None))
        if permission == "none":
            return ("none", None)
        return (permission, github_id)

    github_app.get_collaborator_record = get_collaborator_record


def _url(operation: str, bounty: str, ids: dict) -> str:
    if operation == "list":
        return "/api/bounties"
    if operation == "bounty":
        return f"/api/bounties/{ids[bounty]}"
    if operation == "submission":
        return f"/api/submissions/{ids['submission']}"
    raise ValueError(f"unknown operation {operation}")


async def _send(app, url: str, token: str | None) -> tuple[int, str]:
    import httpx

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=10.0
    ) as client:
        response = await client.get(url, headers=headers)
    return response.status_code, response.text


def _perform(operation: str, actor: str, bounty: str) -> dict:
    """One read against a fresh import of the application under test."""
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    _prepare_env()
    _enable_sqlite_types()
    from app.database import Base, get_db
    from app.main import app as fastapi_app
    from app.middleware.auth import create_access_token
    from app.services import repo_visibility

    reset = getattr(repo_visibility, "reset_repo_access_cache", None)
    if reset is not None:
        asyncio.run(reset())
    _patch_github()
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        users, ids = _seed(session)
        bridge = _Bridge(session)

        async def override_db():
            yield bridge

        fastapi_app.dependency_overrides[get_db] = override_db
        token = None
        if actor != "anonymous":
            token = create_access_token(users[actor].id)
        status, body = asyncio.run(_send(fastapi_app, _url(operation, bounty, ids), token))
        return {"status": status, "body": _clip(body)}
    finally:
        session.close()
        engine.dispose()


def _observe(outcome, actor: str):
    outcome.leaked = False
    if outcome.status != "ok" or not isinstance(outcome.value, dict):
        return outcome
    body = outcome.value.get("body")
    if not isinstance(body, str):
        body = ""
    capture.canary(_CANARY, "tenant-b")
    if _identity(actor) == "tenant-a" and _CANARY in body:
        capture.authorization(
            identity="tenant-a",
            channel="response",
            body=body,
            location="marketplace-response",
        )
        outcome.leaked = True
    return outcome


class World(BaseWorld):
    """One in-process read at a time.

    Actors: ``anonymous``; ``outsider`` (signed in, GitHub linked, no access);
    ``reader`` (signed in, read access); ``requester``; ``funder``;
    ``submitter`` (author of the private submission). Bounty keys: ``public``,
    ``private``. ``read_submission`` accepts ``private`` only.
    """

    child_timeout = 30.0

    def read_bounty(self, actor: str, bounty: str):
        """Return one bounty read.

        ``actor`` is an actor name. ``bounty`` is ``public`` or ``private``.
        The child outcome's ``value`` is ``{"status", "body"}``.
        """
        actor = _require_actor(actor)
        bounty = _require_bounty(bounty)
        _prepare_env()
        return _observe(self.call(_perform, "bounty", actor, bounty), actor)

    def read_submission(self, actor: str, bounty: str):
        """Return one submission read for the private bounty.

        ``actor`` is an actor name. ``bounty`` must be ``private``.
        """
        actor = _require_actor(actor)
        bounty = _require_bounty(bounty)
        if bounty != "private":
            raise ValueError("bounty must be private")
        _prepare_env()
        return _observe(self.call(_perform, "submission", actor, bounty), actor)

    def list_bounties(self, actor: str):
        """Return one bounty-list read for ``actor``."""
        actor = _require_actor(actor)
        _prepare_env()
        return _observe(self.call(_perform, "list", actor, "public"), actor)
