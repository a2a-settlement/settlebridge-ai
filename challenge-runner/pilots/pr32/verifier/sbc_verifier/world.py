"""Verifier-owned SettleBridge world for the PR #32 confidentiality challenge.

Runs the real ``app.main`` application on an in-memory SQLite database. Tenant B
owns a private-repo bounty whose text carries frozen canary tokens. Every response
and log line produced while an identity that is not currently authorized for that
bounty is acting is recorded as ``tenant-a`` in the capture directory, where the
``authorization_confidentiality`` observer looks for the canaries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

VERIFIER_ROOT = Path(os.environ.get("SBC_VERIFIER_ROOT", "/verifier"))
CAPTURE_DIR = Path(os.environ.get("SBC_CAPTURE_DIR", str(VERIFIER_ROOT / "capture")))

PRIVATE_REPO = "tenantb/private-app"
PUBLIC_REPO = "tenantb/public-app"
DISABLED_REPO = "tenantb/disabled-app"
INSTALLATION_ID = 1
READ_PERMISSIONS = frozenset({"read", "triage", "write", "maintain", "admin"})
PRIVATE_REWARD = 424242

_NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
_MAX_ROWS = 3000
_MAX_BODY = 8192
_MAX_BYTES = 900_000
_WINDOW = 1500

_BOUNTIES_DDL = (
    "CREATE TABLE bounties ("
    "id CHAR(32) PRIMARY KEY, requester_id CHAR(32) NOT NULL, title VARCHAR(500) NOT NULL, "
    "description TEXT NOT NULL, category_id CHAR(32), tags TEXT, acceptance_criteria TEXT, "
    "reward_amount INTEGER NOT NULL, escrow_id VARCHAR(255), status VARCHAR(32) NOT NULL, "
    "deadline DATETIME, max_claims INTEGER, min_reputation FLOAT, difficulty VARCHAR(32) NOT NULL, "
    "auto_approve BOOLEAN, provenance_tier VARCHAR(64) NOT NULL, created_at DATETIME, "
    "updated_at DATETIME, funded_at DATETIME, completed_at DATETIME, expired_at DATETIME, "
    "settlement_structure TEXT, mode VARCHAR(32) NOT NULL)"
)

# name -> (GitHub user id or None, initial collaborator answer on PRIVATE_REPO)
_PEOPLE = {
    "requester": (None, None),
    "funder": (1001, ("admin", 1001)),
    "reader": (2002, ("read", 2002)),
    "outsider": (3003, ("none", None)),
    "lessee": (4004, ("none", None)),
    "mismatch": (5005, ("read", 111)),
    "no_github": (None, None),
    "submitter": (6006, ("none", None)),
}
# Always entitled to the private bounty (or, for the submitter, to their own
# submission), whatever GitHub says.
_OWNERS = frozenset({"requester", "funder", "submitter"})


def canaries() -> list[str]:
    data = json.loads((VERIFIER_ROOT / "canaries.json").read_text(encoding="utf-8"))
    return [str(item) for item in data["canaries"]]


class _Bridge:
    """AsyncSession facade over one synchronous SQLite session."""

    def __init__(self, sync: Session):
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
    def __init__(self, tx):
        self._tx = tx

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._tx.commit()
        else:
            self._tx.rollback()
        return False


class _Recorder:
    """Appends tenant-a observations as JSON lines; canary rows are never dropped."""

    def __init__(self, capture_dir: Path, tokens: list[str]):
        self.dir = capture_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tokens = tokens
        self.rows = 0
        self.bytes = 0
        self.lock = threading.Lock()
        path = self.dir / "canaries.jsonl"
        if not path.exists():
            with path.open("w", encoding="utf-8") as handle:
                for token in tokens:
                    handle.write(json.dumps({"token": token, "owner": "tenant-b"}) + "\n")

    def _clip(self, body: str) -> str:
        if len(body) <= _MAX_BODY:
            return body
        for token in self.tokens:
            at = body.find(token)
            if at >= 0:
                start = max(0, at - _WINDOW)
                return body[start : start + 2 * _WINDOW + len(token)]
        return body[:_MAX_BODY]

    def record(self, *, identity: str, channel: str, location: str, body: str) -> None:
        if identity != "tenant-a":
            return
        hit = any(token in body for token in self.tokens)
        line = json.dumps(
            {
                "identity": identity,
                "channel": channel,
                "location": location[:180],
                "body": self._clip(body),
            }
        )
        with self.lock:
            if not hit and (self.rows >= _MAX_ROWS or self.bytes + len(line) > _MAX_BYTES):
                return
            with (self.dir / "authorization.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self.rows += 1
            self.bytes += len(line) + 1


class _LogTap(logging.Handler):
    def __init__(self, world: "World"):
        super().__init__(level=logging.DEBUG)
        self.world = world

    def emit(self, record: logging.LogRecord) -> None:
        actor = self.world._actor
        if actor is None and not self.world._acting:
            return
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if record.exc_info:
            message += " " + logging.Formatter().formatException(record.exc_info)
        self.world._recorder.record(
            identity=self.world.identity_of(actor),
            channel="log",
            location=f"log {record.name}",
            body=message,
        )


class Client:
    """HTTP client acting as one named person. Responses are recorded for the verifier."""

    def __init__(self, world: "World", name: str | None):
        self._world = world
        self.name = name

    def request(self, method: str, url: str, **kwargs):
        world = self._world
        headers = dict(kwargs.pop("headers", None) or {})
        if self.name is not None and "authorization" not in {k.lower() for k in headers}:
            headers["Authorization"] = f"Bearer {world.bot_id(self.name)}"
        with world._as(self.name):
            response = world._client.request(method, url, headers=headers, **kwargs)
            identity = world.identity_of(self.name)
        head = " ".join(f"{k}: {v}" for k, v in response.headers.items())
        world._recorder.record(
            identity=identity,
            channel="response",
            location=f"{method.upper()} {url.split('?', 1)[0]}",
            body=f"{response.status_code} {head}\n{response.text}",
        )
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


class _Acting:
    def __init__(self, world: "World", name: str | None):
        self.world = world
        self.name = name

    def __enter__(self):
        self.prev = (self.world._actor, self.world._acting)
        self.world._actor = self.name
        self.world._acting = True
        return self

    def __exit__(self, *exc):
        self.world._actor, self.world._acting = self.prev
        return False


class World:
    """Public surface for challenger tests: see the contract's ``harness`` text."""

    def __init__(self, monkeypatch):
        os.environ.setdefault("MARKETPLACE_ENABLED", "true")
        from fastapi.testclient import TestClient

        from app.config import settings

        monkeypatch.setattr(settings, "MARKETPLACE_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "GITHUB_ENFORCE_KYA_LINEAGE_SEPARATION", True, raising=False)
        monkeypatch.setattr(settings, "GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM", False, raising=False)
        self._tokens = canaries()
        self._recorder = _Recorder(CAPTURE_DIR, self._tokens)
        self._actor: str | None = None
        self._acting = False
        self.access = {name: answer for name, (_gid, answer) in _PEOPLE.items()}
        self._effective = dict(self.access)
        self.github_calls: list[tuple] = []
        self.private_repo = PRIVATE_REPO
        self.public_repo = PUBLIC_REPO
        self.disabled_repo = DISABLED_REPO
        self._engine = self._make_engine()
        self._sync = Session(self._engine, expire_on_commit=False)
        self.db = _Bridge(self._sync)
        self._seed()
        self._patch(monkeypatch)
        self.app = self._make_app()
        self._client = TestClient(self.app, raise_server_exceptions=False)
        self._tap = _LogTap(self)
        logging.getLogger().addHandler(self._tap)
        logging.getLogger().setLevel(logging.DEBUG)

    # Public API ---------------------------------------------------------

    def client_as(self, name: str | None) -> Client:
        if name is not None and name not in self.users:
            raise KeyError(name)
        return Client(self, name)

    def bot_id(self, name: str) -> str:
        return self.users[name].exchange_bot_id

    def set_github_access(self, name: str, permission: str, github_user_id: int | None = None):
        """Change what GitHub reports for ``name`` on the private repo.

        A grant counts at once. A revocation counts for the verifier only after
        ``advance_past_access_cache()``, because the documented rule lets SettleBridge
        reuse a read-access answer for up to 5 minutes.
        """
        gid = github_user_id if github_user_id is not None else _PEOPLE[name][0]
        self.access[name] = (permission, gid)
        if self._authorized(name, self.access):
            self._effective[name] = self.access[name]

    def advance_past_access_cache(self):
        """Act as if the 5-minute access cache has expired."""
        from app.services import repo_visibility

        reset = getattr(repo_visibility, "reset_repo_access_cache", None)
        if reset is not None:
            asyncio.run(reset())
        self._effective = dict(self.access)

    def run(self, coro):
        return asyncio.run(coro)

    def identity_of(self, name: str | None) -> str:
        if name in _OWNERS:
            return "tenant-b"
        if name is not None and self._authorized(name, self._effective):
            return "tenant-b"
        return "tenant-a"

    def _authorized(self, name: str, table: dict) -> bool:
        gid = _PEOPLE[name][0]
        answer = table.get(name)
        return bool(
            gid is not None and answer and answer[0] in READ_PERMISSIONS and answer[1] == gid
        )

    def close(self):
        logging.getLogger().removeHandler(self._tap)
        self._client.close()
        self._sync.close()
        self._engine.dispose()

    # Construction -------------------------------------------------------

    def _as(self, name):
        self.holder["user"] = None if name is None else self.users[name]
        return _Acting(self, name)

    def _make_engine(self):
        from app.models.bounty import Bounty  # noqa: F401
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
        )
        from app.models.submission import Submission
        from app.models.user import User

        import app.main  # noqa: F401  (registers every model the app uses)
        from app.database import Base

        eng = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        with eng.begin() as conn:
            conn.execute(text(_BOUNTIES_DDL))
        required = {
            model.__table__.name
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
            )
        }
        for table in Base.metadata.sorted_tables:
            if table.name == "bounties":
                continue
            try:
                table.create(eng)
            except Exception:
                if table.name in required:
                    raise
        return eng

    def _seed(self):
        from app.models.bounty import Bounty, BountyMode, BountyStatus, Difficulty, ProvenanceTier
        from app.models.github import (
            AcceptanceSpec,
            GitHubBountyBinding,
            GitHubIdentity,
            GitHubInstallation,
            GitHubLease,
            GitHubRepoLink,
            LeaseStatus,
        )
        from app.models.submission import Submission, SubmissionStatus
        from app.models.user import User, UserType

        sync = self._sync
        self.users = {}
        for name, (gid, _answer) in _PEOPLE.items():
            kind = UserType.REQUESTER if name == "requester" else UserType.AGENT_OPERATOR
            user = User(
                id=uuid.uuid4(),
                email=f"{name}@example.test",
                display_name=name,
                user_type=kind,
                exchange_bot_id=f"{name}-agent",
            )
            self.users[name] = user
            sync.add(user)
        sync.add(
            GitHubInstallation(
                id=uuid.uuid4(), installation_id=INSTALLATION_ID, account_login="tenantb"
            )
        )
        sync.flush()
        for name, (gid, _answer) in _PEOPLE.items():
            if gid is None:
                continue
            sync.add(
                GitHubIdentity(
                    id=uuid.uuid4(),
                    user_id=self.users[name].id,
                    github_user_id=gid,
                    login=name,
                    verified_at=_NOW,
                )
            )
        sync.flush()
        c = self._tokens
        requester = self.users["requester"].id

        def bounty(title, description, reward, criteria=None, status=BountyStatus.OPEN):
            row = Bounty(
                id=uuid.uuid4(),
                requester_id=requester,
                title=title,
                description=description,
                acceptance_criteria=criteria,
                reward_amount=reward,
                status=status,
                completed_at=_NOW if status == BountyStatus.COMPLETED else None,
                difficulty=Difficulty.MEDIUM,
                provenance_tier=ProvenanceTier.TIER1_SELF_DECLARED,
                max_claims=1,
                auto_approve=False,
                created_at=_NOW,
                updated_at=_NOW,
                mode=BountyMode.PRODUCTION,
            )
            sync.add(row)
            return row

        bounties = {
            "public": bounty("Public bounty", "public description", 867530),
            "private": bounty(
                f"Private bounty {c[0]}",
                f"Fix the token leak in the billing module {c[1]}",
                PRIVATE_REWARD,
                criteria={"must": f"Acceptance: {c[2]}"},
            ),
            "completed": bounty(
                f"Completed private bounty {c[0]}",
                f"Rotate the leaked deploy key {c[1]}",
                PRIVATE_REWARD,
                criteria={"must": f"Acceptance: {c[2]}"},
                status=BountyStatus.COMPLETED,
            ),
            "held": bounty("Held private bounty", "held description", 111),
            "disabled": bounty("Disabled private bounty", "disabled description", 222),
        }
        funder = self.users["funder"].id
        for repo, visibility, enabled in (
            (PUBLIC_REPO, "public", True),
            (PRIVATE_REPO, "private", True),
            (DISABLED_REPO, "private", False),
        ):
            sync.add(
                GitHubRepoLink(
                    id=uuid.uuid4(),
                    installation_id=INSTALLATION_ID,
                    repo_full_name=repo,
                    funder_user_id=funder,
                    enabled=enabled,
                    default_policy={},
                    required_checks=[],
                    visibility=visibility,
                )
            )
        sync.flush()

        def bind(row, repo, issue, state="advertised"):
            binding = GitHubBountyBinding(
                id=uuid.uuid4(),
                bounty_id=row.id,
                repo_full_name=repo,
                issue_number=issue,
                state=state,
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

        bind(bounties["public"], PUBLIC_REPO, 1)
        bind(bounties["private"], PRIVATE_REPO, 7)
        bind(bounties["completed"], PRIVATE_REPO, 9, state="paid")
        held_binding, held_spec = bind(bounties["held"], PRIVATE_REPO, 8)
        bind(bounties["disabled"], DISABLED_REPO, 1)
        lessee = self.users["lessee"]
        sync.add(
            GitHubLease(
                id=uuid.uuid4(),
                binding_id=held_binding.id,
                agent_id=lessee.exchange_bot_id,
                user_id=lessee.id,
                github_user_id=4004,
                spec_id=held_spec.id,
                token_hash="c" * 64,
                status=LeaseStatus.WORK.value,
                work_expires_at=_NOW + timedelta(days=3650),
                hard_expires_at=_NOW + timedelta(days=3650),
                created_at=_NOW,
            )
        )
        self.share_tokens = {key: uuid.uuid4() for key in ("private", "completed", "public")}
        self.submission_ids = {key: uuid.uuid4() for key in ("private", "completed", "public")}
        for key, row, content, score in (
            ("private", bounties["private"], f"private patch {c[3]}", 90),
            ("completed", bounties["completed"], f"completed patch {c[3]}", 95),
            ("public", bounties["public"], "public patch", 80),
        ):
            sync.add(
                Submission(
                    id=self.submission_ids[key],
                    claim_id=uuid.uuid4(),
                    bounty_id=row.id,
                    agent_user_id=self.users["submitter"].id,
                    deliverable={"content": content, "content_type": "text/plain"},
                    status=SubmissionStatus.APPROVED,
                    public_share=True,
                    share_token=self.share_tokens[key],
                    submitted_at=_NOW,
                    score=score,
                )
            )
        sync.commit()
        self.ids = {name: row.id for name, row in bounties.items()}

    def _patch(self, monkeypatch):
        from app.services import github_app, github_leases, trust_gateway
        from app.services.trust_gateway import AgentClaims, TrustTokenError

        world = self

        async def get_collaborator_record(installation_id, repo, username):
            world.github_calls.append((installation_id, repo, username))
            if repo != PRIVATE_REPO:
                return ("none", None)
            return world.access.get(username) or ("none", None)

        def verify(token):
            if not any(user.exchange_bot_id == token for user in world.users.values()):
                raise TrustTokenError("unknown agent")
            return AgentClaims(
                agent_id=token,
                org_id="org-1",
                kya_level=2,
                reputation=1.0,
                scopes=frozenset({"mcp:read"}),
                raw={},
            )

        async def lock_agent(db, agent_id):
            return None

        async def shares_principal(*args, **kwargs):
            return False

        monkeypatch.setattr(
            github_app, "get_collaborator_record", get_collaborator_record, raising=False
        )
        monkeypatch.setattr(trust_gateway, "verify_gateway_token", verify)
        monkeypatch.setattr(github_leases, "lock_agent_for_claim", lock_agent, raising=False)
        monkeypatch.setattr(github_leases, "_shares_principal", shares_principal, raising=False)
        try:
            from app.services import repo_visibility

            reset = getattr(repo_visibility, "reset_repo_access_cache", None)
            if reset is not None:
                asyncio.run(reset())
        except Exception:
            pass

    def _make_app(self):
        from app.database import get_db
        from app.main import app
        from app.middleware.auth import get_current_user, get_optional_user

        self.holder = {"user": None}
        bridge = self.db

        async def override_db():
            yield bridge

        def current_user():
            from fastapi import HTTPException

            user = self.holder["user"]
            if user is None:
                raise HTTPException(status_code=401, detail="Not authenticated")
            return user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_optional_user] = lambda: self.holder["user"]
        app.dependency_overrides[get_current_user] = current_user
        return app
