"""GitHub role conflicts and fix-verification attribution. No database and no network."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest

from app.config import settings
from app.models.github import GitHubLease
from app.models.user import ACCOUNT_KIND_HOUSE
from app.services import github_leases
from app.services.challenge_orchestrator import (
    GITHUB_ROLE_CONFLICTS,
    ROLE_CONFLICTS,
    _CONFLICTS,
    github_roles_conflict,
)
from app.services.solver_identity import (
    HOUSE_INDEPENDENCE_STATEMENT,
    NEUTRAL_INDEPENDENCE_STATEMENT,
)

AGENT = "solver-bot"
FUNDER_AGENT = "funder-bot"
REPO = "acme/widget"


def _run(coro):
    return asyncio.run(coro)


def _sha(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _DB:
    def __init__(self, users=None, rows=None):
        self.users = dict(users or {})
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def get(self, model, pk):
        return self.users.get(pk)

    async def execute(self, stmt, *args, **kwargs):
        rows = self.rows

        class _Result:
            def all(self):
                return list(rows)

        return _Result()


class _Claim:
    def __init__(self):
        self.solver_id = uuid.uuid4()
        self.funder_id = uuid.uuid4()
        self.spec_id = uuid.uuid4()
        self.binding = None
        self.rows = []
        self.solver_github = 4242
        self.funder_github = 777
        self.funder_bot = FUNDER_AGENT
        self.enforce = True
        self.shares = False
        self.share_error = None
        self.share_calls = 0

    def install(self, monkeypatch):
        solver = type(
            "User",
            (),
            {
                "id": self.solver_id,
                "exchange_bot_id": AGENT,
                "exchange_api_key": "solver-key",
                "account_kind": None,
            },
        )()
        funder = type(
            "User",
            (),
            {
                "id": self.funder_id,
                "exchange_bot_id": self.funder_bot,
                "exchange_api_key": "funder-key",
                "account_kind": None,
            },
        )()
        self.binding = type(
            "Binding",
            (),
            {
                "id": uuid.uuid4(),
                "bounty_id": uuid.uuid4(),
                "repo_full_name": REPO,
                "issue_number": 12,
                "current_spec_id": self.spec_id,
                "role_holders": [],
            },
        )()
        spec = type(
            "Spec",
            (),
            {
                "id": self.spec_id,
                "version": 1,
                "content_hash": "ab",
                "test_command": "pytest -q",
                "required_checks": [],
                "policy": {},
                "payment_trigger": "merge_with_checks",
                "required_kya": 0,
                "required_ema": 0.0,
                "amount_ate": 100,
            },
        )()
        link = type(
            "Link",
            (),
            {"repo_full_name": REPO, "funder_user_id": self.funder_id, "installation_id": 9},
        )()
        db = _DB({self.solver_id: solver, self.funder_id: funder}, self.rows)
        world = self

        async def fetch_advertised_binding(db, bounty_id):
            return world.binding

        async def fetch_spec(db, spec_id):
            return spec

        async def fetch_users_for_agent(db, agent_id):
            return [solver]

        async def fetch_identity_for_user(db, user_id):
            if user_id == world.solver_id:
                return type("Identity", (), {"github_user_id": world.solver_github})()
            if user_id == world.funder_id:
                return type("Identity", (), {"github_user_id": world.funder_github})()
            return None

        async def fetch_repo_link(db, repo_full_name):
            return link

        async def fetch_bounty_funder(db, bounty_id):
            return funder

        async def hidden_bounty_ids(db, ids):
            return set()

        async def lock_agent_for_claim(db, agent_id):
            return None

        async def count_active_leases(db, agent_id):
            return 0

        async def fetch_expired_timestamps(db, agent_id, since):
            return []

        async def fetch_active_lease_for_binding(db, binding_id):
            return None

        async def shares_principal(*args, **kwargs):
            world.share_calls += 1
            if world.share_error is not None:
                raise world.share_error
            return world.shares

        monkeypatch.setattr(settings, "GITHUB_ENFORCE_KYA_LINEAGE_SEPARATION", self.enforce)
        monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
        monkeypatch.setattr(settings, "GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM", False)
        monkeypatch.setattr(github_leases, "fetch_advertised_binding", fetch_advertised_binding)
        monkeypatch.setattr(github_leases, "fetch_spec", fetch_spec)
        monkeypatch.setattr(github_leases, "fetch_users_for_agent", fetch_users_for_agent)
        monkeypatch.setattr(github_leases, "fetch_identity_for_user", fetch_identity_for_user)
        monkeypatch.setattr(github_leases, "fetch_repo_link", fetch_repo_link)
        monkeypatch.setattr(github_leases, "fetch_bounty_funder", fetch_bounty_funder)
        monkeypatch.setattr(github_leases, "hidden_bounty_ids", hidden_bounty_ids)
        monkeypatch.setattr(github_leases, "lock_agent_for_claim", lock_agent_for_claim)
        monkeypatch.setattr(github_leases, "count_active_leases", count_active_leases)
        monkeypatch.setattr(github_leases, "fetch_expired_timestamps", fetch_expired_timestamps)
        monkeypatch.setattr(
            github_leases, "fetch_active_lease_for_binding", fetch_active_lease_for_binding
        )
        monkeypatch.setattr(github_leases, "_shares_principal", shares_principal)
        self.db = db
        self.solver = solver
        return self

    def claim(self):
        claims = type("Claims", (), {"agent_id": AGENT, "kya_level": 2, "reputation": 0.9})()
        return _run(github_leases.claim_lease(self.db, self.binding.bounty_id, claims))


def test_marketplace_role_table_is_unchanged():
    assert ROLE_CONFLICTS is _CONFLICTS
    assert _CONFLICTS["solver"] == {"challenger", "verifier", "adjudicator"}
    assert "author" not in _CONFLICTS["solver"]
    assert "solver" not in _CONFLICTS["author"]
    assert "funder_approver" not in _CONFLICTS["solver"]


def test_github_role_pairs():
    assert GITHUB_ROLE_CONFLICTS["solver"] == {"verifier", "challenger", "funder_approver"}
    assert github_roles_conflict("solver", "verifier")
    assert github_roles_conflict("verifier", "solver")
    assert github_roles_conflict("solver", "challenger")
    assert github_roles_conflict("funder_approver", "solver")
    assert not github_roles_conflict("solver", "solver")
    assert not github_roles_conflict("solver", "author")
    assert not github_roles_conflict("solver", "funder")


def test_claim_denied_when_solver_is_the_verifier(monkeypatch):
    world = _Claim()
    world.funder_bot = None
    world.install(monkeypatch)
    world.binding.role_holders = [
        github_leases.GitHubRoleHolder(role="verifier", user_id=world.solver_id)
    ]
    result = world.claim()
    assert result.status_code == 403
    assert result.body == {"error": "role_conflict", "role": "verifier"}
    assert world.db.added == []
    assert world.share_calls == 0


def test_role_conflict_check_skipped_when_fix_verifier_disabled(monkeypatch):
    world = _Claim()
    world.funder_bot = None
    world.install(monkeypatch)
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", False)
    world.binding.role_holders = [
        github_leases.GitHubRoleHolder(role="verifier", user_id=world.solver_id)
    ]
    result = world.claim()
    assert getattr(result, "body", {}).get("error") != "role_conflict"


def test_claim_denied_when_solver_shares_github_id_with_challenger(monkeypatch):
    world = _Claim()
    world.funder_bot = None
    world.install(monkeypatch)
    world.binding.role_holders = [
        github_leases.GitHubRoleHolder(role="challenger", github_user_id=world.solver_github)
    ]
    result = world.claim()
    assert result.status_code == 403
    assert result.body["error"] == "role_conflict"
    assert result.body["role"] == "challenger"
    assert world.db.added == []


def test_claim_denied_when_recorded_participant_is_the_solver(monkeypatch):
    world = _Claim()
    world.funder_bot = None
    world.rows = [("verifier", str(world.solver_id))]
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 403
    assert result.body["role"] == "verifier"
    assert world.db.added == []


def test_claim_allowed_when_verifier_is_a_separate_platform_operator(monkeypatch):
    world = _Claim()
    world.funder_bot = None
    world.rows = [("verifier", "platform:verifier")]
    world.install(monkeypatch)
    result = world.claim()
    assert result.status_code == 201
    assert world.share_calls == 0
    leases = [obj for obj in world.db.added if isinstance(obj, GitHubLease)]
    assert len(leases) == 1
    assert leases[0].user_id == world.solver_id


def test_claim_denied_when_exchange_principal_is_shared_with_funder_approver(monkeypatch):
    world = _Claim()
    world.funder_bot = None
    world.shares = True
    world.install(monkeypatch)
    world.binding.role_holders = [
        github_leases.GitHubRoleHolder(
            role="funder_approver",
            user_id=uuid.uuid4(),
            agent_id="approver-bot",
        )
    ]
    result = world.claim()
    assert result.status_code == 403
    assert result.body == {"error": "role_conflict", "role": "funder_approver"}
    assert world.share_calls == 1
    assert world.db.added == []


def test_principal_lookup_failure_refuses_the_role_check(monkeypatch):
    world = _Claim()
    world.funder_bot = None
    world.share_error = github_leases._PrincipalUnavailable("down")
    world.install(monkeypatch)
    world.binding.role_holders = [
        github_leases.GitHubRoleHolder(role="verifier", user_id=uuid.uuid4(), agent_id="other-bot")
    ]
    result = world.claim()
    assert result.status_code == 502
    assert result.body == {"error": "exchange_unavailable"}
    assert world.db.added == []


def test_funder_self_dealing_stays_self_dealing(monkeypatch):
    world = _Claim().install(monkeypatch)
    world.binding.role_holders = [
        github_leases.GitHubRoleHolder(role="verifier", user_id=world.solver_id)
    ]
    world.funder_id = world.solver_id
    # Re-bind the funder fetch to the solver. install() captured the old funder,
    # so point the link and the bounty funder at the solver user directly.
    solver = world.solver

    async def fetch_bounty_funder(db, bounty_id):
        return solver

    async def fetch_repo_link(db, repo_full_name):
        return type("Link", (), {"funder_user_id": solver.id})()

    monkeypatch.setattr(github_leases, "fetch_bounty_funder", fetch_bounty_funder)
    monkeypatch.setattr(github_leases, "fetch_repo_link", fetch_repo_link)
    result = world.claim()
    assert result.status_code == 403
    assert result.body == {"error": "self_dealing"}


def test_attribute_house_account_is_not_independent():
    user = type(
        "User",
        (),
        {"id": uuid.uuid4(), "exchange_bot_id": "house-bot", "account_kind": ACCOUNT_KIND_HOUSE},
    )()
    result = _run(
        github_leases.attribute_fix_verification(
            user,
            house_principals=(),
            lineage=(),
            verifier="platform:verifier",
            github_user_id=55,
        )
    )
    assert result.solver_kind == "house"
    assert result.solver_principal_sha256 == _sha("agent:house-bot")
    assert result.statement == HOUSE_INDEPENDENCE_STATEMENT
    assert result.denial is None
    assert "does not claim organizational independence" in result.statement
    assert "is independent" not in result.statement.casefold()


def test_attribute_third_party_and_unknown():
    user_id = uuid.uuid4()
    user = type(
        "User",
        (),
        {"id": user_id, "exchange_bot_id": "outsider-bot", "account_kind": None},
    )()
    third = _run(
        github_leases.attribute_fix_verification(
            user,
            house_principals={"agent:house-bot"},
            lineage=(),
            exchange_principals=[],
            verifier=github_leases.GitHubRoleHolder(role="verifier", principal="platform:verifier"),
            github_user_id=88,
        )
    )
    assert third.solver_kind == "third_party"
    assert third.solver_principal_sha256 == _sha("agent:outsider-bot")
    assert third.statement == NEUTRAL_INDEPENDENCE_STATEMENT
    assert third.denial is None

    unknown = _run(
        github_leases.attribute_fix_verification(
            user,
            house_principals=set(),
            lineage=(),
            verifier="platform:verifier",
        )
    )
    assert unknown.solver_kind == "unknown"
    assert unknown.solver_principal_sha256 == _sha("agent:outsider-bot")
    assert unknown.statement == NEUTRAL_INDEPENDENCE_STATEMENT
    assert unknown.denial is None


def test_attribute_denies_solver_who_is_also_the_verifier():
    user_id = uuid.uuid4()
    user = type(
        "User",
        (),
        {"id": user_id, "exchange_bot_id": "outsider-bot", "account_kind": None},
    )()
    result = _run(
        github_leases.attribute_fix_verification(
            user,
            house_principals=set(),
            lineage=(),
            exchange_principals=[],
            verifier=github_leases.GitHubRoleHolder(role="verifier", user_id=user_id),
        )
    )
    assert result.denial is not None
    assert result.denial.status_code == 403
    assert result.denial.body["error"] == "role_conflict"
    assert result.solver_kind == "unknown"
    assert result.statement == NEUTRAL_INDEPENDENCE_STATEMENT

    house = type(
        "User",
        (),
        {"id": user_id, "exchange_bot_id": "house-bot", "account_kind": ACCOUNT_KIND_HOUSE},
    )()
    house_result = _run(
        github_leases.attribute_fix_verification(
            house,
            house_principals=(),
            lineage=(),
            verifier=github_leases.GitHubRoleHolder(role="verifier", user_id=user_id),
        )
    )
    assert house_result.solver_kind == "house"
    assert house_result.statement == HOUSE_INDEPENDENCE_STATEMENT
    assert house_result.denial is not None
    assert house_result.denial.status_code == 403


def test_attribute_funder_link_is_not_third_party():
    user_id = uuid.uuid4()
    user = type(
        "User",
        (),
        {"id": user_id, "exchange_bot_id": None, "account_kind": None},
    )()
    result = _run(
        github_leases.attribute_fix_verification(
            user,
            house_principals=set(),
            lineage=[("funder", f"user:{uuid.uuid4()}")],
            github_user_id=9,
        )
    )
    assert result.solver_kind == "unknown"
    assert result.denial is None
    assert result.statement == NEUTRAL_INDEPENDENCE_STATEMENT
    assert result.solver_principal_sha256 == _sha(f"user:{user_id}")


@pytest.mark.parametrize(
    "role",
    ["challenger", "funder_approver"],
)
def test_attribute_denies_other_conflicting_roles(role):
    user_id = uuid.uuid4()
    user = type("User", (), {"id": user_id, "exchange_bot_id": None, "account_kind": None})()
    holder = github_leases.GitHubRoleHolder(role=role, user_id=user_id)
    result = _run(
        github_leases.attribute_fix_verification(
            user,
            house_principals=set(),
            lineage=(),
            **{role: holder},
        )
    )
    assert result.denial is not None
    assert result.denial.body == {"error": "role_conflict", "role": role}
    assert result.solver_kind == "unknown"
