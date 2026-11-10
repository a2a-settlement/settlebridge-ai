"""Self-serve onboarding routes. In-memory SQLite; GitHub and exchange faked."""

# ruff: noqa: E402

from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

# The route imports get_current_user, whose module builds a passlib CryptContext at import.
_passlib = types.ModuleType("passlib")
_passlib_context = types.ModuleType("passlib.context")


class _CryptContext:
    def __init__(self, *args, **kwargs):
        pass


_passlib_context.CryptContext = _CryptContext
_passlib.context = _passlib_context
sys.modules.setdefault("passlib", _passlib)
sys.modules.setdefault("passlib.context", _passlib_context)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubIdentity, GitHubRepoLink
from app.routes import github_onboarding as routes
from tests.test_github_onboarding import (
    ACTIONS,
    REPO,
    Bridge,
    FakeGitHub,
    make_engine,
    make_user,
    reload,
    seed,
)

URL = f"/api/v1/github/repos/{REPO}/onboard"
BODY = {"checks": [{"name": "test", "app_id": ACTIONS}]}


@pytest.fixture()
def db():
    eng = make_engine()
    bridge = Bridge(Session(eng, expire_on_commit=False))
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


@pytest.fixture()
def gh(monkeypatch):
    return FakeGitHub().install(monkeypatch)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(routes.settings, "GITHUB_SELF_SERVE_ONBOARDING", True)


@pytest.fixture(autouse=True)
def balance(monkeypatch):
    from app.services import github_onboarding

    async def open_cost(db, user_id):
        return 0

    monkeypatch.setattr(
        github_onboarding.exchange,
        "get_balance_dashboard",
        lambda account_id: {"available": 5_000, "account_id": account_id},
    )
    monkeypatch.setattr(github_onboarding, "sum_advertised_cost", open_cost)


def _client(db: Bridge, user) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/github")

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _setup(db: Bridge, *, identity: bool = True, **link_values):
    user = make_user()
    db.sync.add(user)
    if identity:
        db.sync.add(
            GitHubIdentity(
                id=uuid.uuid4(),
                user_id=user.id,
                github_user_id=4242,
                login="pat",
                verified_at=datetime.now(timezone.utc),
            )
        )
    seed(db.sync, **link_values)
    db.sync.commit()
    return user


def test_routes_are_hidden_when_self_serve_is_off(db, gh, monkeypatch):
    user = _setup(db)
    monkeypatch.setattr(routes.settings, "GITHUB_SELF_SERVE_ONBOARDING", False)
    client = _client(db, user)
    assert client.get(URL).status_code == 404
    assert client.post(URL, json=BODY).status_code == 404


def test_requires_a_linked_github_identity(db, gh):
    user = _setup(db, identity=False)
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "link_github"
    assert response.json()["detail"]["url"].endswith("/dashboard/settings/github")


@pytest.mark.parametrize(
    "record", [("write", 4242), ("maintain", 4242), ("none", None), ("admin", 999)]
)
def test_requires_admin_on_the_same_github_user(db, gh, record):
    user = _setup(db)
    gh.record = record
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "repo_access"
    assert reload(db).enabled is False


def test_unknown_repo_looks_like_no_access(db, gh):
    user = _setup(db)
    gh.record = ("none", None)
    unknown = _client(db, user).post("/api/v1/github/repos/acme/other/onboard", json=BODY)
    not_admin = _client(db, user).post(URL, json=BODY)
    assert unknown.status_code == not_admin.status_code == 403
    assert (
        unknown.json()["detail"]["message"].replace("acme/other", REPO)
        == (not_admin.json()["detail"]["message"])
    )


def test_removed_installation_looks_like_no_access(db, gh):
    user = _setup(db, removed=True)
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "repo_access"


def test_invalid_repo_name_is_422(db, gh):
    user = _setup(db)
    response = _client(db, user).get("/api/v1/github/repos/acme/bad%20name/onboard")
    assert response.status_code == 422


def test_github_failure_is_502(db, gh):
    user = _setup(db)
    gh.fail = "get_collaborator_record"
    assert _client(db, user).post(URL, json=BODY).status_code == 502


def test_preview_returns_detection_without_writing(db, gh):
    user = _setup(db)
    response = _client(db, user).get(URL)
    assert response.status_code == 200
    payload = response.json()
    assert payload["detected"]["required_checks"] == [{"name": "test", "app_id": ACTIONS}]
    assert payload["enabled"] is False
    assert payload["funded_by_you"] is False and payload["funded_by_other"] is False
    assert payload["auto_dispatch_alerts"] is False
    assert payload["auto_merge_acceptable"] is False
    assert payload["strict_tests"] is False
    assert payload["funder_cannot_solve"] is True
    assert db.commits == 0


def test_post_requires_explicit_checks(db, gh):
    user = _setup(db)
    client = _client(db, user)
    assert client.post(URL, json={}).status_code == 422
    assert client.post(URL, json={"checks": []}).status_code == 422
    assert client.post(URL, json={**BODY, "funder_email": "x@example.com"}).status_code == 422


def test_admin_onboards_as_the_funder(db, gh):
    user = _setup(db)
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 200
    payload = response.json()
    assert payload["changed"] is True and payload["dry_run"] is False
    link = reload(db)
    assert link.enabled is True
    assert link.funder_user_id == user.id
    assert link.onboarded_by_user_id == user.id
    assert "bounty: $5" in payload["labels"]["created"]


def test_dry_run_rolls_back(db, gh):
    user = _setup(db)
    response = _client(db, user).post(
        URL,
        json={
            **BODY,
            "dry_run": True,
            "auto_dispatch_alerts": True,
            "auto_merge_acceptable": True,
            "strict_tests": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True and body["dry_run"] is True
    assert body["labels"] is None
    assert body["after"]["auto_dispatch_alerts"] is True
    assert body["after"]["auto_merge_acceptable"] is True
    assert body["after"]["strict_tests"] is True
    assert gh.label_calls == []
    link = reload(db)
    assert link.enabled is False
    assert bool(link.auto_dispatch_alerts) is False
    assert bool(link.auto_merge_acceptable) is False
    assert "test_file_deleted" not in link.default_policy["rule_severity"]
    assert link.default_policy["rule_severity"]["star_import"] == "violation"


def test_different_funder_is_409(db, gh):
    user = _setup(db)
    other = make_user("owner@example.com")
    db.sync.add(other)
    db.sync.flush()
    link = reload(db)
    link.funder_user_id = other.id
    db.sync.commit()
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "funded_by_other"
    assert reload(db).funder_user_id == other.id


def test_service_validation_is_422(db, gh):
    user = _setup(db)
    response = _client(db, user).post(URL, json={**BODY, "allowed_paths": ["*"]})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "forbidden_path" and detail["path"] == "*"
    assert ".github" in detail["message"]
    assert reload(db).enabled is False


def test_low_balance_reports_headroom(db, gh, monkeypatch):
    from app.services import github_onboarding

    user = _setup(db)
    monkeypatch.setattr(
        github_onboarding.exchange, "get_balance_dashboard", lambda account_id: {"available": 50}
    )
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "funder_low_balance"
    assert detail["headroom_ate"] == 50 and detail["minimum_ate"] == 101


def _stored_option(db: Bridge, field: str) -> bool:
    from app.services.github_repo_admin import _strict_tests_enabled

    link = reload(db)
    if field == "strict_tests":
        policy = link.default_policy if isinstance(link.default_policy, dict) else {}
        return _strict_tests_enabled(policy)
    return bool(getattr(link, field))


@pytest.mark.parametrize(
    "field",
    ["auto_dispatch_alerts", "auto_merge_acceptable", "strict_tests"],
)
def test_post_sets_unsets_and_omits_each_option(db, gh, field):
    from app.services.github_alerts import STRICT_TEST_RULE_SEVERITY

    user = _setup(db)
    client = _client(db, user)
    turned_on = client.post(URL, json={**BODY, field: True})
    assert turned_on.status_code == 200
    assert turned_on.json()["changed"] is True
    assert turned_on.json()["after"][field] is True
    assert turned_on.json()["before"][field] is False
    assert _stored_option(db, field) is True
    if field == "strict_tests":
        severity = reload(db).default_policy["rule_severity"]
        assert severity["star_import"] == "violation"
        for rule, level in STRICT_TEST_RULE_SEVERITY.items():
            assert severity[rule] == level

    assert client.post(URL, json={**BODY, field: True}).json()["changed"] is False
    assert client.post(URL, json=BODY).json()["changed"] is False
    assert _stored_option(db, field) is True

    turned_off = client.post(URL, json={**BODY, field: False})
    assert turned_off.status_code == 200
    assert turned_off.json()["changed"] is True
    assert turned_off.json()["before"][field] is True
    assert turned_off.json()["after"][field] is False
    assert _stored_option(db, field) is False
    if field == "strict_tests":
        assert reload(db).default_policy["rule_severity"] == {"star_import": "violation"}
    assert client.post(URL, json={**BODY, field: False}).json()["changed"] is False


def test_preview_and_status_expose_current_options(db, gh):
    from app.services.github_alerts import STRICT_TEST_RULE_SEVERITY

    user = _setup(
        db,
        auto_dispatch_alerts=True,
        auto_merge_acceptable=False,
        default_policy={"rule_severity": {"star_import": "violation", **STRICT_TEST_RULE_SEVERITY}},
    )
    client = _client(db, user)
    preview = client.get(URL).json()
    assert preview["auto_dispatch_alerts"] is True
    assert preview["auto_merge_acceptable"] is False
    assert preview["strict_tests"] is True
    assert preview["funder_cannot_solve"] is True
    status_payload = client.get(STATUS_URL).json()
    assert status_payload["auto_dispatch_alerts"] is True
    assert status_payload["auto_merge_acceptable"] is False
    assert status_payload["strict_tests"] is True


# Setup status

STATUS_URL = f"/api/v1/github/repos/{REPO}/setup-status"


def _codes(payload: dict) -> list[str]:
    return [step["code"] for step in payload["next_steps"]]


def test_setup_status_is_hidden_when_self_serve_is_off(db, gh, monkeypatch):
    user = _setup(db)
    monkeypatch.setattr(routes.settings, "GITHUB_SELF_SERVE_ONBOARDING", False)
    assert _client(db, user).get(STATUS_URL).status_code == 404


def test_setup_status_ready_for_a_funded_admin(db, gh):
    user = _setup(db)
    response = _client(db, user).get(STATUS_URL)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert _codes(payload) == ["funder_cannot_solve"]
    assert payload["next_steps"][0]["blocking"] is False
    assert "@pat" in payload["next_steps"][0]["message"]
    assert payload["repo_access"] is True and payload["enabled"] is False
    assert payload["auto_dispatch_alerts"] is False
    assert payload["auto_merge_acceptable"] is False
    assert payload["strict_tests"] is False
    assert payload["detected"]["required_checks"] == [{"name": "test", "app_id": ACTIONS}]
    assert payload["funder"] == {
        "exchange_account": True,
        "headroom_ate": 5_000,
        "minimum_ate": 101,
    }
    assert payload["urls"]["install_app"] == (
        "https://github.com/apps/settlebridge-bounties/installations/new"
    )
    assert db.commits == 0


def test_setup_status_lists_every_missing_step_without_repo_details(db, gh):
    user = _setup(db, identity=False)
    user.exchange_bot_id = None
    db.sync.commit()
    payload = _client(db, user).get(STATUS_URL).json()
    assert payload["ready"] is False
    assert _codes(payload) == ["link_github", "funder_no_exchange"]
    assert "funder_cannot_solve" not in _codes(payload)
    assert payload["next_steps"][0]["url"].endswith("/dashboard/settings/github")
    assert payload["detected"] is None and payload["enabled"] is None
    assert payload["auto_dispatch_alerts"] is None
    assert payload["auto_merge_acceptable"] is None
    assert payload["strict_tests"] is None


@pytest.mark.parametrize("record", [("write", 4242), ("admin", 999)])
def test_setup_status_hides_repo_state_from_non_admins(db, gh, record):
    user = _setup(db, enabled=True)
    gh.record = record
    payload = _client(db, user).get(STATUS_URL).json()
    assert _codes(payload) == ["repo_access", "funder_cannot_solve"]
    assert payload["repo_access"] is False
    assert payload["detected"] is None and payload["enabled"] is None
    assert payload["auto_dispatch_alerts"] is None and payload["strict_tests"] is None
    unknown = _client(db, user).get("/api/v1/github/repos/acme/other/setup-status").json()
    assert _codes(unknown) == ["repo_access", "funder_cannot_solve"] and unknown["enabled"] is None


def test_setup_status_asks_for_a_check_and_funding(db, gh, monkeypatch):
    from app.services import github_onboarding

    user = _setup(db)
    gh.rules = []
    gh.runs = [{"name": "ci", "conclusion": "success", "app": {"id": ACTIONS}}]
    monkeypatch.setattr(
        github_onboarding.exchange, "get_balance_dashboard", lambda account_id: {"available": 10}
    )
    payload = _client(db, user).get(STATUS_URL).json()
    assert _codes(payload) == ["no_pinned_check", "funder_low_balance", "funder_cannot_solve"]
    assert payload["next_steps"][0]["candidates"] == [{"name": "ci", "app_id": ACTIONS}]
    assert payload["ready"] is False


def test_setup_status_points_funding_steps_at_add_funds(db, gh, monkeypatch):
    from app.services import github_onboarding

    user = _setup(db)
    monkeypatch.setattr(github_onboarding.settings, "GITHUB_SETUP_FUNDING_ENABLED", True)
    monkeypatch.setattr(
        github_onboarding.exchange, "get_balance_dashboard", lambda account_id: {"available": 10}
    )
    payload = _client(db, user).get(STATUS_URL).json()
    (low,) = [step for step in payload["next_steps"] if step["code"] == "funder_low_balance"]
    assert low["action"] == "add_funds" and "Add funds" in low["message"]
    assert low["url"].endswith("/dashboard/github/setup")


def test_setup_status_reports_another_funder(db, gh):
    user = _setup(db)
    other = make_user("owner@example.com")
    db.sync.add(other)
    db.sync.flush()
    reload(db).funder_user_id = other.id
    db.sync.commit()
    payload = _client(db, user).get(STATUS_URL).json()
    assert _codes(payload) == ["funded_by_other", "funder_cannot_solve"]
    assert payload["funded_by_other"] is True and payload["ready"] is False


def test_setup_status_warns_for_a_private_repo(db, gh):
    user = _setup(db, visibility="private")
    gh.repository["visibility"] = "private"
    gh.repository["private"] = True
    payload = _client(db, user).get(STATUS_URL).json()
    steps = {step["code"]: step for step in payload["next_steps"]}
    assert steps["private_repo_access"]["blocking"] is False
    assert steps["private_repo_access"]["message"] == (
        "Anyone with read access on GitHub can see and claim these bounties. "
        "Add solvers as collaborators."
    )
    assert "forking_disabled" not in steps
    assert payload["visibility"] == "private"
    assert payload["detected"]["allow_forking"] is None
    assert payload["ready"] is True


def test_setup_status_warns_when_a_private_repo_disallows_forks(db, gh):
    user = _setup(db, visibility="internal")
    gh.repository["visibility"] = "internal"
    gh.repository["private"] = True
    gh.repository["allow_forking"] = False
    payload = _client(db, user).get(STATUS_URL).json()
    steps = {step["code"]: step for step in payload["next_steps"]}
    assert steps["private_repo_access"]["blocking"] is False
    assert steps["forking_disabled"]["blocking"] is False
    assert steps["forking_disabled"]["message"] == (
        "This repo does not allow forks. Solvers need write access to push a branch."
    )
    assert payload["visibility"] == "internal"
    assert payload["detected"]["allow_forking"] is False


def test_setup_status_public_repo_has_neither_private_step(db, gh):
    user = _setup(db, visibility="public")
    gh.repository["allow_forking"] = False
    payload = _client(db, user).get(STATUS_URL).json()
    assert "private_repo_access" not in _codes(payload)
    assert "forking_disabled" not in _codes(payload)
    assert payload["visibility"] == "public"
    assert payload["detected"]["allow_forking"] is False


def test_setup_status_notes_an_enabled_repo_without_blocking(db, gh):
    user = _setup(db)
    link = reload(db)
    link.funder_user_id = user.id
    link.enabled = True
    db.sync.commit()
    payload = _client(db, user).get(STATUS_URL).json()
    assert _codes(payload) == ["already_enabled", "funder_cannot_solve"]
    assert payload["ready"] is True and payload["enabled"] is True


def test_setup_status_github_outage_is_502(db, gh):
    user = _setup(db)
    gh.fail = "get_collaborator_record"
    response = _client(db, user).get(STATUS_URL)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "github_unavailable"


# Installation repos

INSTALL_URL = "/api/v1/github/setup/installations/10/repos"


def _add_link(db: Bridge, repo: str, **values) -> None:
    db.sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=10,
            repo_full_name=repo,
            funder_user_id=None,
            enabled=False,
            default_policy={},
            required_checks=[],
            **values,
        )
    )
    db.sync.commit()


def test_installation_repos_lists_only_admin_repos(db, gh, monkeypatch):
    user = _setup(db)
    _add_link(db, "acme/secret")

    async def record(installation_id, repo, username):
        return ("admin", 4242) if repo == REPO else ("read", 4242)

    monkeypatch.setattr(routes.github_app, "get_collaborator_record", record)
    payload = _client(db, user).get(INSTALL_URL).json()
    assert payload["github_login"] == "pat"
    assert payload["repos"] == [
        {"repo": REPO, "enabled": False, "funded_by_you": False, "funded_by_other": False}
    ]
    assert payload["truncated"] is False


def test_installation_repos_skips_repos_github_hides(db, gh, monkeypatch):
    user = _setup(db)

    async def record(installation_id, repo, username):
        raise routes.github_app.GitHubAPIError("forbidden", 403)

    monkeypatch.setattr(routes.github_app, "get_collaborator_record", record)
    assert _client(db, user).get(INSTALL_URL).json()["repos"] == []


def test_installation_repos_unknown_or_removed_installation_is_empty(db, gh):
    user = _setup(db, removed=True)
    client = _client(db, user)
    assert client.get(INSTALL_URL).json()["repos"] == []
    assert client.get("/api/v1/github/setup/installations/99/repos").json()["repos"] == []


def test_installation_repos_requires_identity(db, gh):
    user = _setup(db, identity=False)
    response = _client(db, user).get(INSTALL_URL)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "link_github"


def test_installation_repos_caps_lookups(db, gh, monkeypatch):
    user = _setup(db)
    monkeypatch.setattr(routes, "MAX_INSTALLATION_REPOS", 2)
    for index in range(3):
        _add_link(db, f"acme/extra{index}")
    calls = []

    async def record(installation_id, repo, username):
        calls.append(repo)
        return ("admin", 4242)

    monkeypatch.setattr(routes.github_app, "get_collaborator_record", record)
    payload = _client(db, user).get(INSTALL_URL).json()
    assert len(calls) == 2 and len(payload["repos"]) == 2
    assert payload["truncated"] is True


MINE_URL = "/api/v1/github/repos/mine"


def _fund(db: Bridge, repo: str, funder_user_id, **values) -> None:
    db.sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=10,
            repo_full_name=repo,
            funder_user_id=funder_user_id,
            enabled=values.pop("enabled", True),
            default_policy={},
            required_checks=[],
            **values,
        )
    )
    db.sync.commit()


def test_my_repos_lists_only_repos_the_caller_funds(db, gh):
    user = _setup(db)
    other = make_user("other@example.com")
    db.sync.add(other)
    _fund(db, "acme/zeta", user.id, visibility="private")
    _fund(db, "acme/alpha", user.id, enabled=False, visibility="public")
    _fund(db, "acme/theirs", other.id)
    response = _client(db, user).get(MINE_URL)
    assert response.status_code == 200
    assert response.json()["repos"] == [
        {"repo": "acme/alpha", "enabled": False, "visibility": "public"},
        {"repo": "acme/zeta", "enabled": True, "visibility": "private"},
    ]


def test_my_repos_skips_removed_installations(db, gh):
    user = _setup(db, removed=True)
    _fund(db, "acme/gone", user.id)
    assert _client(db, user).get(MINE_URL).json()["repos"] == []


def test_my_repos_is_hidden_when_self_serve_is_off(db, gh, monkeypatch):
    user = _setup(db)
    monkeypatch.setattr(routes.settings, "GITHUB_SELF_SERVE_ONBOARDING", False)
    assert _client(db, user).get(MINE_URL).status_code == 404
