"""GitHub repo admin service and operator CLI. In-memory SQLite, no network."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import re
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
from app.models.github import GitHubInstallation, GitHubRepoLink
from app.models.user import User, UserType
from app.services.github_alerts import STRICT_TEST_RULE_SEVERITY
from app.services.github_repo_admin import (
    RepoAdminError,
    configure_repo_link,
    list_repo_links,
    set_repo_enabled,
)

REPO = "Acme/Widget"


class _Bridge:
    """Async session facade over a sync SQLite session. Counts commit and rollback."""

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


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    User.__table__.create(eng)
    GitHubInstallation.__table__.create(eng)
    GitHubRepoLink.__table__.create(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def db(engine):
    bridge = _Bridge(Session(engine, expire_on_commit=False))
    try:
        yield bridge
    finally:
        assert bridge.commits == 0
        bridge.sync.close()


def _user(email: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="super-secret-password",
        display_name="Funder",
        user_type=UserType.REQUESTER,
        exchange_api_key="exchange-secret-value",
    )


def _installation(installation_id: int, *, removed: bool = False) -> GitHubInstallation:
    return GitHubInstallation(
        id=uuid.uuid4(),
        installation_id=installation_id,
        account_login="acme",
        removed_at=datetime.now(timezone.utc) if removed else None,
    )


def _policy(**overrides) -> dict:
    policy = {
        "rule_severity": {"star_import": "violation"},
        "allowed_paths": ["old/*"],
        "allowed_dependency_changes": ["oldpkg"],
        "type_ignore_is_suppression": False,
    }
    policy.update(overrides)
    return policy


def _link(installation_id: int, repo: str = REPO, **overrides) -> GitHubRepoLink:
    values = {
        "id": uuid.uuid4(),
        "installation_id": installation_id,
        "repo_full_name": repo,
        "funder_user_id": None,
        "enabled": False,
        "default_policy": _policy(),
        "required_checks": [],
        "visibility": "public",
    }
    values.update(overrides)
    return GitHubRepoLink(**values)


def _add(db: _Bridge, *objects: object) -> None:
    db.sync.add_all(objects)
    db.sync.flush()


async def _reload(db: _Bridge, repo: str) -> GitHubRepoLink:
    db.sync.expire_all()
    stmt = select(GitHubRepoLink).where(
        func.lower(GitHubRepoLink.repo_full_name) == repo.casefold()
    )
    return (await db.execute(stmt)).scalar_one()


def _ready(db: _Bridge, repo: str = REPO, installation_id: int = 10, **flags) -> User:
    user = _user("Pat@Example.com")
    objects: list = [user]
    if flags.get("install", True):
        objects.append(_installation(installation_id, removed=flags.get("removed", False)))
    objects.append(
        _link(
            installation_id,
            repo,
            funder_user_id=user.id if flags.get("funder", True) else None,
            required_checks=[{"name": "ci", "app_id": 7}] if flags.get("checks", True) else [],
            enabled=flags.get("enabled", False),
            visibility=flags.get("visibility", "public"),
        )
    )
    _add(db, *objects)
    return user


def test_list_repo_links(db):
    user = _user("Pat@Example.com")
    _add(
        db,
        user,
        _installation(1),
        _installation(2, removed=True),
        _link(
            1,
            "acme/widget",
            default_policy=_policy(allowed_paths=[], allowed_dependency_changes=[]),
        ),
        _link(
            2,
            "Zen/Lib",
            funder_user_id=user.id,
            enabled=True,
            required_checks=[{"name": "ci", "app_id": 9}],
            default_policy=_policy(
                allowed_paths=["lib/*"],
                allowed_dependency_changes=["pytest"],
                extra={"keep": True},
            ),
        ),
    )

    assert _run(list_repo_links(db)) == [
        {
            "repo": "acme/widget",
            "installation_id": 1,
            "installation_removed": False,
            "enabled": False,
            "strict_tests": False,
            "auto_merge_acceptable": False,
            "auto_dispatch_alerts": False,
            "funder_email": None,
            "required_checks": [],
            "allowed_paths": [],
            "allowed_dependency_changes": [],
            "onboarded_by_user_id": None,
            "onboarded_at": None,
        },
        {
            "repo": "Zen/Lib",
            "installation_id": 2,
            "installation_removed": True,
            "enabled": True,
            "strict_tests": False,
            "auto_merge_acceptable": False,
            "auto_dispatch_alerts": False,
            "funder_email": "Pat@Example.com",
            "required_checks": [{"name": "ci", "app_id": 9}],
            "allowed_paths": ["lib/*"],
            "allowed_dependency_changes": ["pytest"],
            "onboarded_by_user_id": None,
            "onboarded_at": None,
        },
    ]


def test_configure_funder_email_is_case_insensitive_and_leaves_other_fields(db):
    user = _user("Pat@Example.com")
    other = _user("Other@Example.com")
    _add(
        db,
        user,
        other,
        _installation(10),
        _link(10, required_checks=[{"name": "ci", "app_id": 7}]),
    )

    result = _run(
        configure_repo_link(db, "  acme/widget  ", funder_email=" pat@example.com ")
    )
    assert result == {
        "repo": REPO,
        "before": {"funder_email": None},
        "after": {"funder_email": "Pat@Example.com"},
    }
    row = _run(_reload(db, REPO))
    assert row.funder_user_id == user.id
    assert row.repo_full_name == REPO
    assert row.required_checks == [{"name": "ci", "app_id": 7}]
    assert row.default_policy["allowed_paths"] == ["old/*"]
    assert row.default_policy["rule_severity"] == {"star_import": "violation"}

    swapped = _run(configure_repo_link(db, REPO, funder_email="other@example.com"))
    assert swapped["before"] == {"funder_email": "Pat@Example.com"}
    assert swapped["after"] == {"funder_email": "Other@Example.com"}
    assert _run(_reload(db, REPO)).funder_user_id == other.id


def test_configure_checks_replace_the_list_and_can_clear_it(db):
    _add(db, _installation(10), _link(10))
    result = _run(
        configure_repo_link(
            db,
            REPO,
            checks=[
                {"name": " org:ci ", "app_id": 15368},
                {"name": "lint", "app_id": 15368},
            ],
        )
    )
    assert result["before"] == {"required_checks": []}
    assert result["after"] == {
        "required_checks": [
            {"name": "org:ci", "app_id": 15368},
            {"name": "lint", "app_id": 15368},
        ]
    }
    row = _run(_reload(db, REPO))
    assert row.required_checks == result["after"]["required_checks"]
    assert row.default_policy["allowed_paths"] == ["old/*"]
    assert row.funder_user_id is None

    cleared = _run(configure_repo_link(db, REPO, checks=[]))
    assert cleared["after"] == {"required_checks": []}
    assert _run(_reload(db, REPO)).required_checks == []


def test_configure_preserves_unrelated_policy_keys(db):
    _add(
        db,
        _installation(10),
        _link(
            10,
            default_policy={
                "rule_severity": {"star_import": "violation"},
                "allowed_paths": ["old/*"],
                "allowed_dependency_changes": ["oldpkg"],
                "type_ignore_is_suppression": False,
                "extra": {"keep": True},
            },
        ),
    )
    result = _run(configure_repo_link(db, REPO, allowed_paths=[" src/* ", "tests/*"]))
    assert result["before"] == {"allowed_paths": ["old/*"]}
    assert result["after"] == {"allowed_paths": ["src/*", "tests/*"]}
    assert "rule_severity" not in result["before"]
    row = _run(_reload(db, REPO))
    assert row.default_policy == {
        "rule_severity": {"star_import": "violation"},
        "allowed_paths": ["src/*", "tests/*"],
        "allowed_dependency_changes": ["oldpkg"],
        "type_ignore_is_suppression": False,
        "extra": {"keep": True},
    }

    _run(configure_repo_link(db, REPO, allowed_dependency_changes=["requests"]))
    row = _run(_reload(db, REPO))
    assert row.default_policy["allowed_paths"] == ["src/*", "tests/*"]
    assert row.default_policy["allowed_dependency_changes"] == ["requests"]
    assert row.default_policy["extra"] == {"keep": True}
    assert row.default_policy["type_ignore_is_suppression"] is False
    assert row.default_policy["rule_severity"] == {"star_import": "violation"}


def test_configure_rejects_blank_policy_entries(db):
    _add(db, _installation(10), _link(10))
    with pytest.raises(RepoAdminError, match="non-empty string"):
        _run(configure_repo_link(db, REPO, allowed_paths=[""]))
    with pytest.raises(RepoAdminError, match="non-empty string"):
        _run(configure_repo_link(db, REPO, allowed_dependency_changes=["  "]))
    row = _run(_reload(db, REPO))
    assert row.default_policy["allowed_paths"] == ["old/*"]
    assert row.default_policy["allowed_dependency_changes"] == ["oldpkg"]


def test_configure_unknown_funder_email(db):
    _add(db, _user("other@example.com"), _installation(10), _link(10))
    with pytest.raises(RepoAdminError, match="^no user with email missing@example.com$"):
        _run(configure_repo_link(db, REPO, funder_email="missing@example.com"))
    assert _run(_reload(db, REPO)).funder_user_id is None


def test_configure_rejects_ambiguous_funder_email(db):
    _add(
        db,
        _user("Pat@Example.com"),
        _user("pat@example.com"),
        _installation(10),
        _link(10),
    )
    with pytest.raises(
        RepoAdminError,
        match="^funder email matches more than one user: Pat@Example.com$",
    ):
        _run(configure_repo_link(db, REPO, funder_email="Pat@Example.com"))
    assert _run(_reload(db, REPO)).funder_user_id is None


@pytest.mark.parametrize(
    ("checks", "fragment"),
    [
        ([{"name": "", "app_id": 1}], "non-empty string"),
        ([{"name": "   ", "app_id": 1}], "non-empty string"),
        ([{"name": "ci", "app_id": 0}], "positive integer"),
        ([{"name": "ci", "app_id": -1}], "positive integer"),
        ([{"name": "ci", "app_id": True}], "positive integer"),
        ([{"name": "ci", "app_id": "7"}], "positive integer"),
        ([{"name": 7, "app_id": 1}], "non-empty string"),
        (["ci"], "must be an object"),
        ({"name": "ci", "app_id": 1}, "must be a list"),
    ],
)
def test_configure_rejects_bad_checks(db, checks, fragment):
    _add(db, _installation(10), _link(10))
    with pytest.raises(RepoAdminError, match=fragment):
        _run(configure_repo_link(db, REPO, checks=checks))
    assert _run(_reload(db, REPO)).required_checks == []


def test_configure_rejects_duplicate_checks_without_partial_update(db):
    user = _user("Pat@Example.com")
    _add(db, user, _installation(10), _link(10))
    with pytest.raises(RepoAdminError, match="^duplicate check ci:7$"):
        _run(
            configure_repo_link(
                db,
                REPO,
                funder_email="pat@example.com",
                checks=[{"name": "ci", "app_id": 7}, {"name": " ci ", "app_id": 7}],
            )
        )
    row = _run(_reload(db, REPO))
    assert row.funder_user_id is None
    assert row.required_checks == []


def test_configure_and_enable_require_an_existing_link(db):
    with pytest.raises(RepoAdminError, match="install the GitHub App on this repo first"):
        _run(configure_repo_link(db, "missing/repo", funder_email="a@b.c"))
    with pytest.raises(RepoAdminError, match="install the GitHub App on this repo first"):
        _run(set_repo_enabled(db, "missing/repo", True))
    with pytest.raises(RepoAdminError, match="install the GitHub App on this repo first"):
        _run(set_repo_enabled(db, "missing/repo", False))
    assert db.sync.execute(select(GitHubRepoLink)).scalars().all() == []


def test_enable_lists_every_missing_item(db):
    _ready(db, funder=False, checks=False, removed=True)
    with pytest.raises(RepoAdminError) as caught:
        _run(set_repo_enabled(db, "ACME/widget", True))
    assert str(caught.value) == (
        "cannot enable Acme/Widget: missing funder, missing required check, installation removed"
    )
    assert _run(_reload(db, REPO)).enabled is False


@pytest.mark.parametrize(
    ("flags", "repo", "message"),
    [
        ({"funder": False}, REPO, "cannot enable Acme/Widget: missing funder"),
        ({"checks": False}, REPO, "cannot enable Acme/Widget: missing required check"),
        ({"removed": True}, REPO, "cannot enable Acme/Widget: installation removed"),
        (
            {"install": False},
            "Ghost/Repo",
            "cannot enable Ghost/Repo: missing installation",
        ),
    ],
)
def test_enable_names_each_missing_item(db, flags, repo, message):
    _ready(db, repo=repo, **flags)
    with pytest.raises(RepoAdminError, match=f"^{re.escape(message)}$"):
        _run(set_repo_enabled(db, repo.casefold(), True))
    assert _run(_reload(db, repo)).enabled is False


def test_enable_succeeds_when_funder_checks_and_installation_are_present(db):
    _ready(db)
    result = _run(set_repo_enabled(db, "acme/widget", True))
    assert result == {
        "repo": REPO,
        "before": {"enabled": False},
        "after": {"enabled": True},
    }
    row = _run(_reload(db, REPO))
    assert row.enabled is True
    assert row.repo_full_name == REPO


def test_disable_works_without_funder_or_checks(db):
    _add(db, _installation(10, removed=True), _link(10, enabled=True))
    result = _run(set_repo_enabled(db, "ACME/widget", False))
    assert result == {
        "repo": REPO,
        "before": {"enabled": True},
        "after": {"enabled": False},
    }
    assert _run(_reload(db, REPO)).enabled is False


def test_repo_match_is_case_insensitive_and_does_not_create_a_link(db):
    user = _ready(db)
    _run(configure_repo_link(db, "ACME/widget", allowed_paths=["src/*"]))
    _run(set_repo_enabled(db, "acme/WIDGET", True))
    _run(set_repo_enabled(db, "Acme/Widget", False))
    db.sync.expire_all()
    rows = db.sync.execute(select(GitHubRepoLink)).scalars().all()
    assert len(rows) == 1
    assert rows[0].repo_full_name == REPO
    assert rows[0].enabled is False
    assert rows[0].funder_user_id == user.id
    assert rows[0].default_policy["allowed_paths"] == ["src/*"]
    assert rows[0].default_policy["type_ignore_is_suppression"] is False


def test_parse_check_splits_on_the_last_colon():
    assert github_repos.parse_check("windows:check:15368") == {
        "name": "windows:check",
        "app_id": 15368,
    }
    assert github_repos.parse_check("ci:7") == {"name": "ci", "app_id": 7}
    assert github_repos.parse_check("  my check:12") == {"name": "my check", "app_id": 12}
    for bad in ("ci", "ci:", ":7", "ci:0", "ci:-1", "ci:1.5", "ci:+7", "ci:01x"):
        with pytest.raises(argparse.ArgumentTypeError):
            github_repos.parse_check(bad)


def _seed(engine, *objects) -> None:
    session = Session(engine, expire_on_commit=False)
    try:
        session.add_all(objects)
        session.commit()
    finally:
        session.close()


def _stored_link(engine, repo: str) -> dict | None:
    session = Session(engine, expire_on_commit=False)
    try:
        row = session.execute(
            select(GitHubRepoLink).where(
                func.lower(GitHubRepoLink.repo_full_name) == repo.casefold()
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "repo": row.repo_full_name,
            "enabled": bool(row.enabled),
            "funder_user_id": None if row.funder_user_id is None else str(row.funder_user_id),
            "required_checks": json.loads(json.dumps(row.required_checks)),
            "default_policy": json.loads(json.dumps(row.default_policy)),
            "auto_merge_acceptable": bool(row.auto_merge_acceptable),
            "auto_dispatch_alerts": bool(row.auto_dispatch_alerts),
        }
    finally:
        session.close()


def _sessions(monkeypatch, engine) -> list[_Bridge]:
    opened: list[_Bridge] = []

    def factory():
        bridge = _Bridge(Session(engine, expire_on_commit=False))
        opened.append(bridge)
        return bridge

    monkeypatch.setattr(github_repos, "async_session", factory)
    return opened


def _status_payload(out: str) -> tuple[object, str]:
    body, status = out.rstrip("\n").rsplit("\n", 1)
    return json.loads(body), status


def _assert_no_secrets(text: str) -> None:
    assert "super-secret-password" not in text
    assert "exchange-secret-value" not in text
    assert "postgresql" not in text.lower()
    assert "sqlite" not in text.lower()


def test_cli_list_does_not_write(engine, monkeypatch, capsys):
    user = _user("Pat@Example.com")
    _seed(
        engine,
        user,
        _installation(10),
        _link(
            10,
            enabled=True,
            funder_user_id=user.id,
            required_checks=[{"name": "ci", "app_id": 7}],
        ),
    )
    opened = _sessions(monkeypatch, engine)
    code = github_repos.main(["list"])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert "DRY RUN" not in out
    assert "APPLIED" not in out
    payload = json.loads(out)
    assert payload[0]["repo"] == REPO
    assert payload[0]["funder_email"] == "Pat@Example.com"
    assert payload[0]["enabled"] is True
    assert payload[0]["installation_removed"] is False
    assert payload[0]["required_checks"] == [{"name": "ci", "app_id": 7}]
    _assert_no_secrets(out)
    assert opened[0].commits == 0
    assert _stored_link(engine, REPO)["enabled"] is True


def test_cli_dry_run_rolls_back(engine, monkeypatch, capsys):
    user = _user("Pat@Example.com")
    _seed(
        engine,
        user,
        _installation(10),
        _link(10, required_checks=[{"name": "ci", "app_id": 1}]),
    )
    opened = _sessions(monkeypatch, engine)
    code = github_repos.main(
        ["configure", "--repo", "acme/widget", "--funder-email", "pat@example.com"]
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload, status = _status_payload(out)
    assert status == "DRY RUN (use --apply to write)"
    assert payload["before"]["funder_email"] is None
    assert payload["after"]["funder_email"] == "Pat@Example.com"
    stored = _stored_link(engine, REPO)
    assert stored["funder_user_id"] is None
    assert stored["required_checks"] == [{"name": "ci", "app_id": 1}]
    assert opened[0].commits == 0
    assert opened[0].rollbacks == 1
    _assert_no_secrets(out)


def test_cli_apply_commits_and_splits_check_on_last_colon(engine, monkeypatch, capsys):
    user = _user("Pat@Example.com")
    _seed(
        engine,
        user,
        _installation(10),
        _link(10, required_checks=[{"name": "old", "app_id": 1}]),
    )
    opened = _sessions(monkeypatch, engine)
    code = github_repos.main(
        [
            "configure",
            "--repo",
            "Acme/Widget",
            "--funder-email",
            "pat@example.com",
            "--check",
            "windows:check:15368",
            "--check",
            "lint:4",
            "--allowed-path",
            "src/*",
            "--allowed-dependency",
            "requests",
            "--apply",
        ]
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload, status = _status_payload(out)
    assert status == "APPLIED"
    assert "DRY RUN" not in out
    assert payload["after"]["required_checks"] == [
        {"name": "windows:check", "app_id": 15368},
        {"name": "lint", "app_id": 4},
    ]
    assert payload["after"]["allowed_paths"] == ["src/*"]
    assert payload["after"]["allowed_dependency_changes"] == ["requests"]
    stored = _stored_link(engine, REPO)
    assert stored["funder_user_id"] == str(user.id)
    assert stored["required_checks"] == payload["after"]["required_checks"]
    assert stored["default_policy"]["allowed_paths"] == ["src/*"]
    assert stored["default_policy"]["allowed_dependency_changes"] == ["requests"]
    assert stored["default_policy"]["rule_severity"] == {"star_import": "violation"}
    assert stored["default_policy"]["type_ignore_is_suppression"] is False
    assert opened[0].commits == 1
    _assert_no_secrets(out + err)


def test_cli_enable_apply_commits_and_disable_dry_run_rolls_back(engine, monkeypatch, capsys):
    user = _user("Pat@Example.com")
    _seed(
        engine,
        user,
        _installation(10),
        _link(10, funder_user_id=user.id, required_checks=[{"name": "ci", "app_id": 7}]),
    )
    _sessions(monkeypatch, engine)
    code = github_repos.main(["enable", "--repo", "ACME/widget", "--apply"])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert _status_payload(out)[1] == "APPLIED"
    assert _stored_link(engine, REPO)["enabled"] is True

    code = github_repos.main(["disable", "--repo", "acme/widget"])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert _status_payload(out)[1] == "DRY RUN (use --apply to write)"
    assert _stored_link(engine, REPO)["enabled"] is True


def test_cli_repo_admin_error_exits_2(engine, monkeypatch, capsys):
    opened = _sessions(monkeypatch, engine)
    code = github_repos.main(
        ["configure", "--repo", "missing/repo", "--funder-email", "pat@example.com"]
    )
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert "install the GitHub App on this repo first" in err
    assert opened[0].commits == 0
    assert opened[0].rollbacks == 1
    _assert_no_secrets(out + err)


def test_cli_configure_without_changes_exits_2(engine, monkeypatch, capsys):
    _seed(engine, _installation(10), _link(10))
    _sessions(monkeypatch, engine)
    code = github_repos.main(["configure", "--repo", REPO])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert "nothing to change" in err
    _assert_no_secrets(out + err)


def test_cli_bad_arguments_exit_2(capsys):
    with pytest.raises(SystemExit) as missing_repo:
        github_repos.main(["enable"])
    assert missing_repo.value.code == 2

    with pytest.raises(SystemExit) as bad_check:
        github_repos.main(["configure", "--repo", "acme/widget", "--check", "nocolon"])
    assert bad_check.value.code == 2
    assert "NAME:APP_ID" in capsys.readouterr().err

    with pytest.raises(SystemExit) as zero_app:
        github_repos.main(["configure", "--repo", "acme/widget", "--check", "ci:0"])
    assert zero_app.value.code == 2


def test_configure_strict_tests_merges_rules_and_persists_flags(db):
    policy = _policy()
    policy["rule_severity"] = {"star_import": "violation", "test_file_deleted": "review"}
    _add(db, _installation(10), _link(10, default_policy=policy))

    turned_on = _run(
        configure_repo_link(
            db,
            REPO,
            strict_tests=True,
            auto_merge_acceptable=True,
            auto_dispatch_alerts=True,
        )
    )
    assert turned_on["before"]["strict_tests"] is False
    assert turned_on["after"]["strict_tests"] is True
    assert turned_on["before"]["auto_merge_acceptable"] is False
    assert turned_on["after"]["auto_merge_acceptable"] is True
    assert turned_on["after"]["auto_dispatch_alerts"] is True
    row = _run(_reload(db, REPO))
    assert row.auto_merge_acceptable is True
    assert row.auto_dispatch_alerts is True
    assert row.default_policy["rule_severity"] == {
        "star_import": "violation",
        **STRICT_TEST_RULE_SEVERITY,
    }
    assert row.default_policy["allowed_paths"] == ["old/*"]
    assert row.default_policy["allowed_dependency_changes"] == ["oldpkg"]
    assert row.default_policy["type_ignore_is_suppression"] is False
    listed = _run(list_repo_links(db))
    assert listed[0]["strict_tests"] is True
    assert listed[0]["auto_merge_acceptable"] is True
    assert listed[0]["auto_dispatch_alerts"] is True

    _run(configure_repo_link(db, REPO, allowed_paths=["src/*"]))
    row = _run(_reload(db, REPO))
    assert row.auto_merge_acceptable is True
    assert row.auto_dispatch_alerts is True
    assert row.default_policy["rule_severity"]["assertion_changed"] == "violation"
    assert row.default_policy["allowed_paths"] == ["src/*"]

    turned_off = _run(
        configure_repo_link(
            db,
            REPO,
            strict_tests=False,
            auto_merge_acceptable=False,
            auto_dispatch_alerts=False,
        )
    )
    assert turned_off["before"]["strict_tests"] is True
    assert turned_off["after"]["strict_tests"] is False
    assert turned_off["after"]["auto_merge_acceptable"] is False
    assert turned_off["after"]["auto_dispatch_alerts"] is False
    row = _run(_reload(db, REPO))
    assert row.default_policy["rule_severity"] == {"star_import": "violation"}
    assert row.default_policy["allowed_paths"] == ["src/*"]
    assert row.auto_merge_acceptable is False
    assert row.auto_dispatch_alerts is False
    listed = _run(list_repo_links(db))
    assert listed[0]["strict_tests"] is False
    assert listed[0]["auto_merge_acceptable"] is False
    assert listed[0]["auto_dispatch_alerts"] is False


def test_strict_tests_off_removes_those_keys_at_any_level(db):
    _add(
        db,
        _installation(10),
        _link(
            10,
            default_policy={
                "rule_severity": {
                    "star_import": "violation",
                    "test_file_deleted": "review",
                    "assertion_changed": "violation",
                    "weak_assertion_added": "review",
                    "test_skip_added": "violation",
                    "other": "review",
                },
                "allowed_paths": ["old/*"],
            },
        ),
    )
    _run(configure_repo_link(db, REPO, strict_tests=False))
    row = _run(_reload(db, REPO))
    assert row.default_policy["rule_severity"] == {"star_import": "violation", "other": "review"}
    assert row.default_policy["allowed_paths"] == ["old/*"]


def test_strict_tests_off_drops_rule_severity_when_nothing_remains(db):
    _add(
        db,
        _installation(10),
        _link(
            10,
            default_policy={
                "rule_severity": dict(STRICT_TEST_RULE_SEVERITY),
                "allowed_paths": ["keep/*"],
                "extra": {"keep": True},
            },
        ),
    )
    _run(configure_repo_link(db, REPO, strict_tests=False))
    row = _run(_reload(db, REPO))
    assert row.default_policy == {"allowed_paths": ["keep/*"], "extra": {"keep": True}}
    assert _run(list_repo_links(db))[0]["strict_tests"] is False


def test_list_strict_tests_requires_every_rule_at_violation(db):
    rules = dict(STRICT_TEST_RULE_SEVERITY)
    rules["test_skip_added"] = "review"
    _add(
        db,
        _installation(10),
        _link(10, auto_merge_acceptable=True, default_policy=_policy(rule_severity=rules)),
    )
    listed = _run(list_repo_links(db))[0]
    assert listed["strict_tests"] is False
    assert listed["auto_merge_acceptable"] is True
    assert listed["auto_dispatch_alerts"] is False


@pytest.mark.parametrize(
    "field",
    ["strict_tests", "auto_merge_acceptable", "auto_dispatch_alerts"],
)
def test_configure_rejects_non_bool_auto_flags(db, field):
    _add(db, _installation(10), _link(10, auto_merge_acceptable=True))
    with pytest.raises(RepoAdminError, match=f"^{field} must be true or false$"):
        _run(configure_repo_link(db, REPO, **{field: 1}))
    row = _run(_reload(db, REPO))
    assert row.auto_merge_acceptable is True
    assert row.auto_dispatch_alerts is False
    assert "test_file_deleted" not in row.default_policy["rule_severity"]


def test_cli_parses_strict_tests_and_auto_flags():
    parser = github_repos.build_parser()
    omitted = parser.parse_args(["configure", "--repo", "acme/widget"])
    assert omitted.strict_tests is None
    assert omitted.auto_merge_acceptable is None
    assert omitted.auto_dispatch_alerts is None

    enabled = parser.parse_args(
        [
            "configure",
            "--repo",
            "acme/widget",
            "--strict-tests",
            "--auto-merge",
            "--auto-dispatch-alerts",
        ]
    )
    assert enabled.strict_tests is True
    assert enabled.auto_merge_acceptable is True
    assert enabled.auto_dispatch_alerts is True

    disabled = parser.parse_args(
        [
            "configure",
            "--repo",
            "acme/widget",
            "--no-strict-tests",
            "--no-auto-merge",
            "--no-auto-dispatch-alerts",
        ]
    )
    assert disabled.strict_tests is False
    assert disabled.auto_merge_acceptable is False
    assert disabled.auto_dispatch_alerts is False


def test_cli_apply_persists_strict_tests_and_auto_flags(engine, monkeypatch, capsys):
    _seed(engine, _installation(10), _link(10))
    _sessions(monkeypatch, engine)
    code = github_repos.main(
        [
            "configure",
            "--repo",
            "acme/widget",
            "--strict-tests",
            "--auto-merge",
            "--no-auto-dispatch-alerts",
            "--apply",
        ]
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    payload, status = _status_payload(out)
    assert status == "APPLIED"
    assert payload["after"]["strict_tests"] is True
    assert payload["after"]["auto_merge_acceptable"] is True
    assert payload["after"]["auto_dispatch_alerts"] is False
    stored = _stored_link(engine, REPO)
    severity = stored["default_policy"]["rule_severity"]
    assert severity["star_import"] == "violation"
    for rule, level in STRICT_TEST_RULE_SEVERITY.items():
        assert severity[rule] == level
    assert stored["auto_merge_acceptable"] is True
    assert stored["auto_dispatch_alerts"] is False
    _assert_no_secrets(out + err)

    code = github_repos.main(
        ["configure", "--repo", REPO, "--strict-tests", "--auto-dispatch-alerts"]
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert _status_payload(out)[1] == "DRY RUN (use --apply to write)"
    stored = _stored_link(engine, REPO)
    assert stored["auto_merge_acceptable"] is True
    assert stored["auto_dispatch_alerts"] is False
    assert "test_file_deleted" in stored["default_policy"]["rule_severity"]

    code = github_repos.main(
        ["configure", "--repo", REPO, "--no-strict-tests", "--no-auto-merge", "--apply"]
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert _status_payload(out)[1] == "APPLIED"
    stored = _stored_link(engine, REPO)
    assert stored["default_policy"]["rule_severity"] == {"star_import": "violation"}
    assert stored["auto_merge_acceptable"] is False
    assert stored["auto_dispatch_alerts"] is False
    _assert_no_secrets(out + err)


@pytest.mark.parametrize("visibility", ["private", "internal"])
def test_enable_allows_a_private_or_internal_repo(db, visibility):
    _ready(db, visibility=visibility)
    result = _run(set_repo_enabled(db, REPO, True))
    assert result["after"]["enabled"] is True
    row = _run(_reload(db, REPO))
    assert row.enabled is True
    assert row.visibility == visibility


def test_enable_refuses_unknown_visibility(db):
    _ready(db, visibility=None)
    with pytest.raises(RepoAdminError, match="visibility is unknown"):
        _run(set_repo_enabled(db, REPO, True))
    assert _run(_reload(db, REPO)).enabled is False


def test_disable_allows_a_repo_that_is_not_public(db):
    _ready(db, visibility="internal", enabled=True)
    result = _run(set_repo_enabled(db, REPO, False))
    assert result["after"]["enabled"] is False
    assert _run(_reload(db, REPO)).enabled is False
    assert _run(_reload(db, REPO)).visibility == "internal"


def test_auto_dispatch_allows_a_private_repo(db):
    _add(db, _installation(10), _link(10, visibility="private"))
    result = _run(configure_repo_link(db, REPO, auto_dispatch_alerts=True))
    assert result["after"]["auto_dispatch_alerts"] is True
    row = _run(_reload(db, REPO))
    assert row.auto_dispatch_alerts is True
    assert row.default_policy["allowed_paths"] == ["old/*"]


def test_auto_dispatch_can_be_turned_off_on_a_private_repo(db):
    _add(
        db,
        _installation(10),
        _link(10, visibility="private", auto_dispatch_alerts=True),
    )
    result = _run(configure_repo_link(db, REPO, auto_dispatch_alerts=False))
    assert result["after"]["auto_dispatch_alerts"] is False
    assert _run(_reload(db, REPO)).auto_dispatch_alerts is False
