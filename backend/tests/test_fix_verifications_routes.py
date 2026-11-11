"""Fix-verification HTTP routes. In-memory SQLite, verifier_client mocked, no network."""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings, settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.fix_verifier import FixVerification, VerifierQuota
from app.models.github import GitHubInstallation, GitHubRepoLink
from app.routes.fix_verifications import router
from app.services import fix_verifier
from tests.test_verifier_quota import INSTALLATION, REPO, open_db

# Hex letters keep SQLite's NUMERIC affinity on a UUID column from storing the id as a float.
OWNER = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
FUNDER = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
OPERATOR = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc3")
STRANGER = uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd4")
BASE = "a" * 40
FIX = "b" * 40
VULN = "GHSA-xxxx-yyyy-zzzz"
SECRET = "secret-repro-steps"
URL = "/api/v1/fix-verifications"


def _body(**overrides) -> dict:
    if "advisory" in overrides:
        advisory = overrides.pop("advisory")
    else:
        advisory = {
            "vuln_id": VULN,
            "aliases": ["CVE-2024-0001"],
            "summary": "example",
            "details": "details",
            "package": "example-pkg",
            "ecosystem": "pypi",
        }
    body = {
        "repo_full_name": REPO,
        "pr": 32,
        "base_sha": BASE,
        "fix_sha": FIX,
        "advisory": advisory,
        "solver_kind": "house",
        "solver_principal_id": "alice",
    }
    body.update(overrides)
    return body


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    monkeypatch.setattr(settings, "FIX_VERIFIER_PUBLIC_API", True)
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(OPERATOR))
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 20)
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 10)
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_COST_CAP_CENTS", 2500)
    monkeypatch.setattr(settings, "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION", 500)
    eng, bridge = open_db()
    bridge.sync.add(
        GitHubInstallation(
            id=uuid.uuid4(),
            installation_id=INSTALLATION,
            account_login="acme",
            owner_user_id=OWNER,
        )
    )
    bridge.sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=INSTALLATION,
            repo_full_name=REPO,
            funder_user_id=FUNDER,
            enabled=True,
            fix_verifier_opt_in=True,
            default_policy={},
            required_checks=[],
            auto_dispatch_alerts=False,
            auto_merge_acceptable=False,
        )
    )
    bridge.sync.commit()
    seen: list[dict] = []

    def verify_fn(request):
        seen.append(request)
        return {
            "verdict": "verified",
            "solver_kind": "third_party",
            "cost_cents": 11,
        }

    monkeypatch.setattr(fix_verifier, "verify", verify_fn)
    app = FastAPI()
    app.include_router(router)

    async def override_db():
        try:
            yield bridge
        except Exception:
            await bridge.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OWNER)
    client = TestClient(app)
    try:
        yield {"client": client, "app": app, "bridge": bridge, "seen": seen}
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _as(world, user_id):
    world["app"].dependency_overrides[get_current_user] = lambda user_id=user_id: SimpleNamespace(
        id=user_id
    )


def _quota(world):
    world["bridge"].sync.expire_all()
    return world["bridge"].sync.scalar(select(VerifierQuota))


def _count(world) -> int:
    world["bridge"].sync.expire_all()
    return int(world["bridge"].sync.scalar(select(func.count()).select_from(FixVerification)))


def test_feature_flags_default_off():
    assert Settings.model_fields["FIX_VERIFIER_ENABLED"].default is False
    assert Settings.model_fields["FIX_VERIFIER_PUBLIC_API"].default is False
    assert Settings.model_fields["FIX_VERIFIER_DAILY_QUOTA"].default == 20


def test_public_api_off_is_404_for_every_route_without_work(world, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_PUBLIC_API", False)
    client = world["client"]
    before = _count(world)
    missing = uuid.uuid4()
    created = client.post(URL, json=_body())
    fetched = client.get(f"{URL}/{missing}")
    receipt = client.get(f"{URL}/{missing}/receipt")
    replayed = client.post(f"{URL}/{missing}/replay")
    assert created.status_code == fetched.status_code == receipt.status_code == replayed.status_code
    assert created.status_code == 404
    assert created.json() == fetched.json() == {"detail": "not_found"}
    assert world["seen"] == []
    assert _count(world) == before

    world["app"].dependency_overrides.pop(get_current_user)
    anonymous = client.post(URL, json=_body())
    assert anonymous.status_code == 404
    assert anonymous.json() == {"detail": "not_found"}
    assert world["seen"] == []


def test_anonymous_is_401_when_the_public_api_is_on(world):
    world["app"].dependency_overrides.pop(get_current_user)
    client = world["client"]
    assert client.post(URL, json=_body()).status_code == 401
    assert client.get(f"{URL}/{uuid.uuid4()}").status_code == 401
    assert world["seen"] == []


def test_service_flag_off_hides_create_and_replay_but_not_a_stored_row(world, monkeypatch):
    created = world["client"].post(URL, json=_body())
    assert created.status_code == 201, created.text
    verification_id = created.json()["id"]
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", False)
    world["seen"].clear()
    denied = world["client"].post(URL, json=_body(fix_sha="c" * 40))
    hidden_replay = world["client"].post(f"{URL}/{verification_id}/replay")
    fetched = world["client"].get(f"{URL}/{verification_id}")
    assert denied.status_code == 404
    assert denied.json() == {"detail": "not_found"}
    assert hidden_replay.status_code == 404
    assert fetched.status_code == 200
    assert fetched.json()["verdict"] == "verified"
    assert world["seen"] == []


def test_not_opted_in_is_403_and_does_not_call(world):
    link = world["bridge"].sync.scalar(select(GitHubRepoLink))
    link.fix_verifier_opt_in = False
    world["bridge"].sync.commit()
    denied = world["client"].post(URL, json=_body())
    link.fix_verifier_opt_in = True
    link.enabled = False
    world["bridge"].sync.commit()
    disabled = world["client"].post(URL, json=_body())
    missing = world["client"].post(URL, json=_body(repo_full_name="other/repo"))
    assert denied.status_code == disabled.status_code == missing.status_code == 403
    assert denied.json() == {"detail": "not_opted_in"}
    assert world["seen"] == []
    assert _count(world) == 0


def test_unlinked_pr_and_invalid_body_are_422(world):
    unlinked = world["client"].post(URL, json=_body(advisory={}, alert_ref=None))
    invalid = world["client"].post(URL, json=_body(base_sha="short", solver_kind="house"))
    upgraded = world["client"].post(URL, json=_body(solver_kind="House-bot"))
    assert unlinked.status_code == 422
    assert unlinked.json() == {"detail": "alert_or_advisory_required"}
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "invalid_request"}
    assert upgraded.status_code == 422
    assert world["seen"] == []
    assert _count(world) == 0


def test_create_passes_budget_and_does_not_upgrade_house(world):
    response = world["client"].post(URL, json=_body())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["verdict"] == "verified"
    assert body["solver_kind"] == "house"
    assert body["cost_cents"] == 11
    assert body["repo_full_name"] == REPO
    assert "solver_principal_id" not in body
    assert world["seen"][0]["budget_cents"] == 500
    assert world["seen"][0]["solver_kind"] == "house"
    assert world["seen"][0]["solver_principal_sha256"] == hashlib.sha256(b"alice").hexdigest()
    quota = _quota(world)
    assert quota.used == 1
    assert quota.spend_cents == 11


def test_third_party_claim_from_operator_is_house(world):
    _as(world, OPERATOR)
    response = world["client"].post(
        URL, json=_body(solver_kind="third_party", solver_principal_id="user:someone")
    )
    assert response.status_code == 201, response.text
    assert response.json()["solver_kind"] == "house"
    assert world["seen"][0]["solver_kind"] == "house"


def test_third_party_claim_without_principal_is_unknown(world):
    response = world["client"].post(
        URL, json=_body(solver_kind="third_party", solver_principal_id=None)
    )
    assert response.status_code == 201, response.text
    assert response.json()["solver_kind"] == "unknown"


def test_third_party_claim_with_unrelated_principal_stays_third_party(world):
    response = world["client"].post(
        URL,
        json=_body(solver_kind="third_party", solver_principal_id=f"user:{uuid.UUID(int=9)}"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["solver_kind"] == "third_party"


def test_third_party_claim_naming_a_house_principal_is_house(world, monkeypatch):
    house = f"user:{uuid.UUID(int=7)}"
    monkeypatch.setattr(settings, "PLATFORM_HOUSE_PRINCIPALS", house)
    response = world["client"].post(
        URL, json=_body(solver_kind="third_party", solver_principal_id=house)
    )
    assert response.status_code == 201, response.text
    assert response.json()["solver_kind"] == "house"


def test_cache_hit_costs_nothing_more(world):
    first = world["client"].post(URL, json=_body())
    second = world["client"].post(URL, json=_body(repo_full_name="Acme/Widget"))
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(world["seen"]) == 1
    assert _quota(world).used == 1
    assert _quota(world).spend_cents == 11
    assert _count(world) == 1


def test_unsupported_project_does_not_spend(world, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 0)
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 0)
    response = world["client"].post(
        URL,
        json=_body(advisory={"vuln_id": VULN, "package": "left-pad", "ecosystem": "npm"}),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["verdict"] == "inconclusive"
    assert body["inconclusive_reason"] == "unsupported_project"
    assert body["cost_cents"] == 0
    assert world["seen"] == []
    assert _quota(world) is None


def test_daily_quota_is_429_with_retry_after(world, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 1)
    first = world["client"].post(URL, json=_body())
    assert first.status_code == 201, first.text
    blocked = world["client"].post(URL, json=_body(fix_sha="c" * 40))
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "quota_exceeded"}
    assert blocked.headers["retry-after"].isdigit()
    assert int(blocked.headers["retry-after"]) >= 1
    assert len(world["seen"]) == 1
    assert _quota(world).used == 1
    assert _count(world) == 1


def test_visibility_and_embargo(world, monkeypatch):
    def incomplete(request):
        world["seen"].append(request)
        return {
            "verdict": "incomplete_fix_confirmed",
            "detail": SECRET,
            "reproducer": SECRET,
            "cost_cents": 3,
        }

    monkeypatch.setattr(fix_verifier, "verify", incomplete)
    created = world["client"].post(URL, json=_body())
    assert created.status_code == 201, created.text
    assert SECRET in created.text
    assert created.json()["embargo_state"] == "private"
    assert created.json()["result"]["reproducer"] == SECRET
    verification_id = created.json()["id"]

    _as(world, FUNDER)
    funder = world["client"].get(f"{URL}/{verification_id}")
    assert funder.status_code == 200, funder.text
    assert funder.json()["verdict"] == "incomplete_fix_confirmed"
    assert funder.json()["result"] == {"verdict": "incomplete_fix_confirmed"}
    assert SECRET not in funder.text

    _as(world, OPERATOR)
    operator = world["client"].get(f"{URL}/{verification_id}")
    assert operator.status_code == 200
    assert operator.json()["result"]["reproducer"] == SECRET

    _as(world, STRANGER)
    hidden = world["client"].get(f"{URL}/{verification_id}")
    missing = world["client"].get(f"{URL}/{uuid.uuid4()}")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() == {"detail": "not_found"}
    assert SECRET not in hidden.text
    assert verification_id not in hidden.text


def test_verified_result_is_visible_to_the_funder_and_hidden_from_strangers(world):
    created = world["client"].post(URL, json=_body())
    verification_id = created.json()["id"]
    _as(world, FUNDER)
    funder = world["client"].get(f"{URL}/{verification_id}")
    assert funder.status_code == 200
    assert funder.json()["verdict"] == "verified"
    assert funder.json()["result"]["verdict"] == "verified"
    _as(world, STRANGER)
    hidden = world["client"].get(f"{URL}/{verification_id}")
    missing = world["client"].get(f"{URL}/{uuid.uuid4()}")
    assert hidden.status_code == 404
    assert hidden.json() == missing.json()


def test_receipt_hook_returns_the_stored_id_or_404(world):
    created = world["client"].post(URL, json=_body())
    verification_id = created.json()["id"]
    missing = world["client"].get(f"{URL}/{verification_id}/receipt")
    absent = world["client"].get(f"{URL}/{uuid.uuid4()}/receipt")
    assert missing.status_code == absent.status_code == 404
    assert missing.json() == absent.json() == {"detail": "not_found"}

    world["bridge"].sync.expire_all()
    row = world["bridge"].sync.get(FixVerification, uuid.UUID(verification_id))
    row.receipt_id = "rcpt-9"
    world["bridge"].sync.commit()
    found = world["client"].get(f"{URL}/{verification_id}/receipt")
    assert found.status_code == 200
    assert found.json() == {"receipt_id": "rcpt-9"}

    _as(world, FUNDER)
    assert world["client"].get(f"{URL}/{verification_id}/receipt").json() == {
        "receipt_id": "rcpt-9"
    }
    _as(world, STRANGER)
    hidden = world["client"].get(f"{URL}/{verification_id}/receipt")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "not_found"}
    assert "rcpt-9" not in hidden.text


def test_replay_is_owner_or_operator_and_ignores_the_cache(world):
    created = world["client"].post(URL, json=_body())
    verification_id = created.json()["id"]
    assert len(world["seen"]) == 1

    _as(world, FUNDER)
    funder = world["client"].post(f"{URL}/{verification_id}/replay")
    _as(world, STRANGER)
    stranger = world["client"].post(f"{URL}/{verification_id}/replay")
    missing = world["client"].post(f"{URL}/{uuid.uuid4()}/replay")
    assert funder.status_code == stranger.status_code == missing.status_code == 404
    assert funder.json() == stranger.json() == missing.json() == {"detail": "not_found"}
    assert len(world["seen"]) == 1

    _as(world, OPERATOR)
    replayed = world["client"].post(f"{URL}/{verification_id}/replay")
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["id"] == verification_id
    assert replayed.json()["solver_kind"] == "house"
    assert len(world["seen"]) == 2
    assert _quota(world).used == 2
    assert _quota(world).spend_cents == 22

    _as(world, OWNER)
    again = world["client"].post(f"{URL}/{verification_id}/replay")
    assert again.status_code == 200
    assert len(world["seen"]) == 3
