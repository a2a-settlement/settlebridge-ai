"""Repo onboarding service and the ``onboard`` CLI. In-memory SQLite; GitHub and exchange faked."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from datetime import datetime, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.cli import github_repos
from app.models.github import GitHubIdentity, GitHubInstallation, GitHubRepoLink
from app.models.user import User, UserType
from app.services import github_app, github_onboarding
from app.services.github_onboarding import (
    FunderConflictError,
    OnboardExchangeError,
    OnboardGitHubError,
    candidate_checks,
    checks_from_protection,
    checks_from_rules,
    detect_defaults,
    onboard_repo,
    paths_for,
    validated_allowed_paths,
)
from app.services.github_repo_admin import RepoAdminError

REPO = "acme/widget"
HEAD = "a" * 40
ACTIONS = 15368


class Bridge:
    """Async session facade over a sync SQLite session."""

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

    async def delete(self, obj):
        self.sync.delete(obj)


class FakeGitHub:
    def __init__(self):
        self.repository = {"default_branch": "main"}
        self.branch = {"name": "main", "commit": {"sha": HEAD}}
        self.rules: list[dict] = [_rules_check("test", ACTIONS)]
        self.runs: list[dict] = []
        self.entries = [
            {"name": "pyproject.toml", "type": "file"},
            {"name": "src", "type": "dir"},
            {"name": "tests", "type": "dir"},
            {"name": ".github", "type": "dir"},
        ]
        self.subdirs: dict[str, list[dict]] = {}
        self.probed: list[str] = []
        self.record: tuple[str, int | None] = ("admin", 4242)
        self.fail: str | None = None

    def install(self, monkeypatch):
        def make(name, value):
            async def call(*args, **kwargs):
                if self.fail == name:
                    raise github_app.GitHubAPIError("boom", 503)
                return value() if callable(value) else value

            monkeypatch.setattr(github_app, name, call)

        make("get_repository", lambda: self.repository)
        make("get_branch", lambda: self.branch)
        make("get_branch_rules", lambda: self.rules)
        make("list_check_runs", lambda: self.runs)
        make("get_collaborator_record", lambda: self.record)

        async def entries(installation_id, repo, ref, path=""):
            if self.fail == "list_root_entries":
                raise github_app.GitHubAPIError("boom", 503)
            if not path:
                return self.entries
            self.probed.append(path)
            return self.subdirs.get(path, [])

        monkeypatch.setattr(github_app, "list_root_entries", entries)
        return self


def _rules_check(context: str, integration_id: int | None) -> dict:
    item: dict = {"context": context}
    if integration_id is not None:
        item["integration_id"] = integration_id
    return {
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [item], "strict_required_status_checks_policy": False},
    }


def _run(coro):
    return asyncio.run(coro)


def make_engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for model in (User, GitHubInstallation, GitHubRepoLink, GitHubIdentity):
        model.__table__.create(eng)
    return eng


@pytest.fixture()
def engine():
    eng = make_engine()
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def db(engine):
    bridge = Bridge(Session(engine, expire_on_commit=False))
    try:
        yield bridge
    finally:
        bridge.sync.close()


@pytest.fixture()
def gh(monkeypatch):
    return FakeGitHub().install(monkeypatch)


@pytest.fixture()
def balance(monkeypatch):
    state = {"available": 10_000, "open": 0, "error": None}

    def dashboard(account_id):
        if state["error"] is not None:
            raise state["error"]
        return {"available": state["available"], "account_id": account_id}

    async def open_cost(db, user_id):
        return state["open"]

    monkeypatch.setattr(github_onboarding.exchange, "get_balance_dashboard", dashboard)
    monkeypatch.setattr(github_onboarding, "sum_advertised_cost", open_cost)
    return state


def make_user(email: str = "pat@example.com", *, exchange: bool = True) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="super-secret-password",
        display_name="Funder",
        user_type=UserType.REQUESTER,
        exchange_bot_id="acct-1" if exchange else None,
        exchange_api_key="exchange-secret-value",
    )


def seed(sync: Session, *, removed: bool = False, **link_values) -> GitHubRepoLink:
    values = {
        "id": uuid.uuid4(),
        "installation_id": 10,
        "repo_full_name": REPO,
        "funder_user_id": None,
        "enabled": False,
        "default_policy": {"rule_severity": {"star_import": "violation"}},
        "required_checks": [],
    }
    values.update(link_values)
    link = GitHubRepoLink(**values)
    sync.add(
        GitHubInstallation(
            id=uuid.uuid4(),
            installation_id=10,
            account_login="acme",
            removed_at=datetime.now(timezone.utc) if removed else None,
        )
    )
    sync.add(link)
    sync.flush()
    return link


def reload(db: Bridge) -> GitHubRepoLink:
    db.sync.expire_all()
    stmt = select(GitHubRepoLink).where(func.lower(GitHubRepoLink.repo_full_name) == REPO)
    return db.sync.execute(stmt).scalar_one()


# Detection helpers


def test_checks_from_rules_splits_pinned_and_unpinned():
    rules = [
        _rules_check("test", ACTIONS),
        _rules_check("lint", None),
        {"type": "pull_request", "parameters": {}},
        _rules_check("test", ACTIONS),
    ]
    assert checks_from_rules(rules) == ([{"name": "test", "app_id": ACTIONS}], ["lint"])


def test_checks_from_protection_reads_checks_and_legacy_contexts():
    branch = {
        "protection": {
            "required_status_checks": {
                "checks": [{"context": "test", "app_id": ACTIONS}, {"context": "docs", "app_id": None}],
                "contexts": ["test", "legacy"],
            }
        }
    }
    assert checks_from_protection(branch) == (
        [{"name": "test", "app_id": ACTIONS}],
        ["docs", "legacy"],
    )
    assert checks_from_protection({"protected": False}) == ([], [])


def test_candidate_checks_keep_only_successful_runs_with_an_app():
    runs = [
        {"name": "test", "conclusion": "success", "app": {"id": ACTIONS}},
        {"name": "test", "conclusion": "success", "app": {"id": ACTIONS}},
        {"name": "lint", "conclusion": "failure", "app": {"id": ACTIONS}},
        {"name": "build", "conclusion": "success", "app": {}},
        {"name": "audit", "conclusion": "success", "app": {"id": 99}},
    ]
    assert candidate_checks(runs) == [
        {"name": "audit", "app_id": 99},
        {"name": "test", "app_id": ACTIONS},
    ]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (
            [("pyproject.toml", "file"), ("src", "dir"), ("tests", "dir"), ("docs", "dir")],
            ("python", ["src/*", "tests/*"]),
        ),
        (
            [("setup.py", "file"), ("app", "dir"), ("test", "dir")],
            ("python", ["app/*", "test/*"]),
        ),
        (
            [("package.json", "file"), ("lib", "dir"), ("test", "dir"), ("app", "dir")],
            ("javascript", ["lib/*", "test/*"]),
        ),
        ([("Makefile", "file"), ("src", "dir")], (None, [])),
        ([("src", "file"), ("pyproject.toml", "file")], ("python", [])),
    ],
)
def test_paths_for_uses_only_directories_that_exist(entries, expected):
    assert paths_for([{"name": n, "type": t} for n, t in entries]) == expected


@pytest.mark.parametrize(
    "glob", ["*", "**", "*.yml", ".github/*", ".github/workflows/*", "*/*", ".git*"]
)
def test_allowed_paths_reject_globs_that_reach_github_config(glob):
    with pytest.raises(RepoAdminError, match=r"\.github"):
        validated_allowed_paths(["src/*", glob])


def test_allowed_paths_must_not_be_empty():
    with pytest.raises(RepoAdminError, match="allows every path"):
        validated_allowed_paths([])
    assert validated_allowed_paths([" src/* ", "tests/*"]) == ["src/*", "tests/*"]


# detect_defaults


def test_detect_prefers_branch_rules(db, gh):
    seed(db.sync)
    gh.runs = [{"name": "test", "conclusion": "success", "app": {"id": ACTIONS}}]
    gh.branch["protection"] = {
        "required_status_checks": {"checks": [{"context": "other", "app_id": 5}]}
    }
    detected = _run(detect_defaults(db, "ACME/Widget"))
    assert detected == {
        "repo": REPO,
        "installation_id": 10,
        "default_branch": "main",
        "head_sha": HEAD,
        "required_checks": [{"name": "test", "app_id": ACTIONS}],
        "required_checks_source": "branch_rules",
        "unpinned_required_checks": [],
        "candidate_checks": [{"name": "test", "app_id": ACTIONS}],
        "ecosystem": "python",
        "package_dirs": [],
        "allowed_paths": ["src/*", "tests/*"],
    }


def test_detect_adds_python_package_directories(db, gh):
    seed(db.sync)
    gh.entries = [
        {"name": "pyproject.toml", "type": "file"},
        {"name": "pilot", "type": "dir"},
        {"name": "tools", "type": "dir"},
        {"name": "tests", "type": "dir"},
        {"name": "docs", "type": "dir"},
        {"name": ".github", "type": "dir"},
        {"name": "__pycache__", "type": "dir"},
    ]
    gh.subdirs = {
        "pilot": [{"name": "__init__.py", "type": "file"}, {"name": "text.py", "type": "file"}],
        "tools": [{"name": "run.py", "type": "file"}, {"name": "__init__.py", "type": "dir"}],
    }
    detected = _run(detect_defaults(db, REPO))
    assert gh.probed == ["pilot", "tools"]
    assert detected["package_dirs"] == ["pilot"]
    assert detected["allowed_paths"] == ["pilot/*", "tests/*"]


def test_detect_probes_at_most_ten_directories(db, gh):
    seed(db.sync)
    gh.entries = [{"name": "setup.py", "type": "file"}] + [
        {"name": f"pkg{i:02d}", "type": "dir"} for i in range(15)
    ]
    _run(detect_defaults(db, REPO))
    assert gh.probed == [f"pkg{i:02d}" for i in range(10)]


def test_detect_does_not_probe_javascript_repos(db, gh):
    seed(db.sync)
    gh.entries = [{"name": "package.json", "type": "file"}, {"name": "widget", "type": "dir"}]
    detected = _run(detect_defaults(db, REPO))
    assert gh.probed == []
    assert detected["package_dirs"] == [] and detected["allowed_paths"] == []


def test_onboard_ignores_allowed_path_order(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(
        db.sync,
        funder_user_id=user.id,
        enabled=True,
        required_checks=[{"name": "test", "app_id": ACTIONS}],
        default_policy={"allowed_paths": ["tests/*", "src/*"]},
    )
    assert _run(onboard_repo(db, REPO, funder=user))["changed"] is False


def test_detect_falls_back_to_branch_protection(db, gh):
    seed(db.sync)
    gh.rules = []
    gh.branch["protection"] = {
        "required_status_checks": {"checks": [{"context": "ci", "app_id": ACTIONS}]}
    }
    detected = _run(detect_defaults(db, REPO))
    assert detected["required_checks"] == [{"name": "ci", "app_id": ACTIONS}]
    assert detected["required_checks_source"] == "branch_protection"


def test_detect_reports_runs_only_as_candidates(db, gh):
    seed(db.sync)
    gh.rules = [_rules_check("test", None)]
    gh.runs = [{"name": "test", "conclusion": "success", "app": {"id": ACTIONS}}]
    detected = _run(detect_defaults(db, REPO))
    assert detected["required_checks"] == []
    assert detected["required_checks_source"] is None
    assert detected["unpinned_required_checks"] == ["test"]
    assert detected["candidate_checks"] == [{"name": "test", "app_id": ACTIONS}]


def test_detect_requires_a_live_installation(db, gh):
    seed(db.sync, removed=True)
    with pytest.raises(RepoAdminError, match="installation"):
        _run(detect_defaults(db, REPO))


def test_detect_requires_an_installed_repo(db, gh):
    with pytest.raises(RepoAdminError, match="install the GitHub App"):
        _run(detect_defaults(db, REPO))


def test_detect_wraps_github_errors(db, gh):
    seed(db.sync)
    gh.fail = "get_branch_rules"
    with pytest.raises(OnboardGitHubError):
        _run(detect_defaults(db, REPO))


# onboard_repo


def test_onboard_configures_and_enables_then_is_idempotent(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(db.sync)

    first = _run(onboard_repo(db, REPO, funder=user))
    assert first["changed"] is True
    assert first["after"] == {
        "funder_email": "pat@example.com",
        "required_checks": [{"name": "test", "app_id": ACTIONS}],
        "allowed_paths": ["src/*", "tests/*"],
        "enabled": True,
    }
    assert first["funder_headroom_ate"] == 10_000
    link = reload(db)
    assert link.enabled is True
    assert link.funder_user_id == user.id
    assert link.default_policy == {
        "rule_severity": {"star_import": "violation"},
        "allowed_paths": ["src/*", "tests/*"],
    }
    assert link.onboarded_by_user_id == user.id
    assert link.onboarded_at is not None
    stamped = link.onboarded_at

    second = _run(onboard_repo(db, REPO, funder=user))
    assert second["changed"] is False
    assert second["before"] == {} and second["after"] == {}
    assert reload(db).onboarded_at == stamped


def test_onboard_passed_values_win(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(db.sync)
    result = _run(
        onboard_repo(
            db,
            REPO,
            funder=user,
            checks=[{"name": "unit", "app_id": 7}],
            allowed_paths=["pkg/*"],
            allowed_dependency_changes=["requests"],
            actor_user_id=None,
        )
    )
    assert result["after"]["required_checks"] == [{"name": "unit", "app_id": 7}]
    link = reload(db)
    assert link.default_policy["allowed_paths"] == ["pkg/*"]
    assert link.default_policy["allowed_dependency_changes"] == ["requests"]


def test_onboard_without_a_pinned_check_lists_candidates(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(db.sync)
    gh.rules = []
    gh.runs = [{"name": "test", "conclusion": "success", "app": {"id": ACTIONS}}]
    with pytest.raises(RepoAdminError, match=r"pass --check NAME:APP_ID.*test:15368"):
        _run(onboard_repo(db, REPO, funder=user))
    assert reload(db).enabled is False


def test_onboard_requires_allowed_paths(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(db.sync)
    gh.entries = [{"name": "Makefile", "type": "file"}]
    with pytest.raises(RepoAdminError, match="allowed path"):
        _run(onboard_repo(db, REPO, funder=user))


def test_onboard_rejects_github_globs(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(db.sync)
    with pytest.raises(RepoAdminError, match=r"\.github"):
        _run(onboard_repo(db, REPO, funder=user, allowed_paths=["*"]))


def test_onboard_requires_an_exchange_account(db, gh, balance):
    user = make_user(exchange=False)
    db.sync.add(user)
    seed(db.sync)
    with pytest.raises(RepoAdminError, match="no exchange account"):
        _run(onboard_repo(db, REPO, funder=user))


def test_onboard_requires_headroom_for_a_one_dollar_bounty(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(db.sync)
    balance["available"] = 600
    balance["open"] = 500
    with pytest.raises(RepoAdminError, match="has 100 ATE free.*needs 101 ATE"):
        _run(onboard_repo(db, REPO, funder=user))
    balance["available"] = 601
    assert _run(onboard_repo(db, REPO, funder=user))["funder_headroom_ate"] == 101


def test_onboard_reports_exchange_failures(db, gh, balance):
    user = make_user()
    db.sync.add(user)
    seed(db.sync)
    balance["error"] = ValueError("A2A_DASHBOARD_API_KEY not configured on this server")
    with pytest.raises(OnboardExchangeError, match="could not read"):
        _run(onboard_repo(db, REPO, funder=user))


def test_onboard_refuses_a_different_funder_unless_replacing(db, gh, balance):
    owner = make_user("owner@example.com")
    other = make_user("other@example.com")
    db.sync.add_all([owner, other])
    seed(db.sync, funder_user_id=owner.id)
    with pytest.raises(FunderConflictError):
        _run(onboard_repo(db, REPO, funder=other))
    result = _run(onboard_repo(db, REPO, funder=other, replace_funder=True))
    assert result["before"]["funder_email"] == "owner@example.com"
    assert reload(db).funder_user_id == other.id


# CLI


def _sessions(monkeypatch, engine) -> list[Bridge]:
    opened: list[Bridge] = []

    def factory():
        bridge = Bridge(Session(engine, expire_on_commit=False))
        opened.append(bridge)
        return bridge

    monkeypatch.setattr(github_repos, "async_session", factory)
    return opened


def _seed_engine(engine, user: User) -> None:
    with Session(engine) as sync:
        sync.add(user)
        seed(sync)
        sync.commit()


def _split(out: str) -> tuple[dict, str]:
    body, status = out.rstrip("\n").rsplit("\n", 1)
    return json.loads(body), status


def test_cli_parses_onboard_flags():
    args = github_repos.build_parser().parse_args(
        [
            "onboard",
            "--repo",
            REPO,
            "--funder-email",
            "pat@example.com",
            "--check",
            "ci:build:15368",
            "--allowed-path",
            "src/*",
            "--replace-funder",
        ]
    )
    assert args.check == [{"name": "ci:build", "app_id": ACTIONS}]
    assert args.allowed_path == ["src/*"]
    assert args.replace_funder is True and args.apply is False
    with pytest.raises(SystemExit):
        github_repos.build_parser().parse_args(["onboard", "--repo", REPO])


def test_cli_onboard_dry_run_then_apply(engine, gh, balance, monkeypatch, capsys):
    _seed_engine(engine, make_user())
    opened = _sessions(monkeypatch, engine)
    argv = ["onboard", "--repo", REPO, "--funder-email", "PAT@example.com"]

    assert github_repos.main(argv) == 0
    payload, status = _split(capsys.readouterr().out)
    assert status == "DRY RUN (use --apply to write)"
    assert payload["changed"] is True
    assert opened[-1].rollbacks == 1 and opened[-1].commits == 0

    assert github_repos.main([*argv, "--apply"]) == 0
    out = capsys.readouterr().out
    payload, status = _split(out)
    assert status == "APPLIED"
    assert opened[-1].commits == 1
    assert "exchange-secret-value" not in out and "super-secret-password" not in out

    assert github_repos.main([*argv, "--apply"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["changed"] is False
    assert opened[-1].commits == 0


def test_cli_onboard_error_exits_2(engine, gh, balance, monkeypatch, capsys):
    _seed_engine(engine, make_user())
    _sessions(monkeypatch, engine)
    gh.rules = []
    code = github_repos.main(["onboard", "--repo", REPO, "--funder-email", "pat@example.com"])
    assert code == 2
    assert "pass --check NAME:APP_ID" in capsys.readouterr().err
