"""Verifier-bypass bounty. In-memory SQLite, no network."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.fix_verifier import BypassState, FixVerification, VerifierBypassSubmission
from app.routes.verifier_bypasses import router
from app.schemas.verifier_bypass import SUMMARY_MAX_LENGTH, TEST_SOURCE_MAX_LENGTH
from app.services.verifier_bypass import export_corpus_candidates, record_verifier_bypass_reward

URL = "/api/v1/verifier-bypasses"
NOT_FOUND = {"error": "not_found"}
REPO = "acme/widget"
BASE = "a" * 40
VULN = "CVE-2024-9999"
SUMMARY = "BYPASS_SUMMARY_DO_NOT_LEAK the fix is still exploitable"
SOURCE = "def test_bypass():\n    raise AssertionError('BYPASS_REPRO_SOURCE_DO_NOT_EXPORT')\n"
NOTES = "BYPASS_NOTES_DO_NOT_EXPORT"
# Hex letters keep SQLite from coercing these ids (NUMERIC affinity on the UUID type).
REPORTER = uuid.UUID("11111111-1111-4111-8111-11111111111a")
OPERATOR = uuid.UUID("22222222-2222-4222-8222-22222222222b")
STRANGER = uuid.UUID("33333333-3333-4333-8333-33333333333c")
SOLVER = uuid.UUID("44444444-4444-4444-8444-44444444444d")
_FIX_SEQ = 0


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    def add(self, instance):
        self.sync.add(instance)

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()


def _engine():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    FixVerification.__table__.create(eng)
    VerifierBypassSubmission.__table__.create(eng)
    return eng


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "VERIFIER_BYPASS_BOUNTY_ENABLED", False)
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(OPERATOR))
    eng = _engine()
    bridge = _Bridge(Session(eng, expire_on_commit=False))
    principal = SimpleNamespace(id=REPORTER, exchange_bot_id=None)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, app=app, bridge=bridge, principal=principal)
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "VERIFIER_BYPASS_BOUNTY_ENABLED", True)


def _as(world, user_id, exchange_bot_id=None):
    world.principal.id = user_id
    world.principal.exchange_bot_id = exchange_bot_id


def _fix_sha() -> str:
    global _FIX_SEQ
    _FIX_SEQ += 1
    return f"{_FIX_SEQ:040x}"


def _verification(
    world, *, verdict="verified", solver=None, verification_id=None, fix_sha=None
) -> FixVerification:
    row = FixVerification(
        id=verification_id or uuid.uuid4(),
        repo_full_name=REPO,
        vuln_id=VULN,
        base_sha=BASE,
        fix_sha=fix_sha or _fix_sha(),
        state="done",
        verdict=verdict,
        inputs={},
        result={},
        cost_cents=0,
        embargo_state="private",
        solver_kind="third_party",
        solver_principal_id=solver,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    world.bridge.sync.add(row)
    world.bridge.sync.commit()
    return row


def _body(verification_id, **overrides) -> dict:
    payload = {
        "verification_id": str(verification_id),
        "summary": SUMMARY,
        "reproducer": {
            "test_source": SOURCE,
            "failure_class": "integrity",
            "notes": NOTES,
        },
    }
    payload.update(overrides)
    return payload


def _post(world, verification_id, **overrides):
    return world.client.post(URL, json=_body(verification_id, **overrides))


def _decide(world, submission_id, decision, *, reward=None, notes=None):
    payload = {"decision": decision}
    if reward is not None:
        payload["reward_cents"] = reward
    if notes is not None:
        payload["notes"] = notes
    return world.client.post(f"{URL}/{submission_id}/decision", json=payload)


def _rows(world) -> list[VerifierBypassSubmission]:
    return list(world.bridge.sync.execute(select(VerifierBypassSubmission)).scalars().all())


def _hash(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_flag_off_is_404_and_hides_existing_reports(world):
    verification = _verification(world)
    created = _post(world, verification.id)
    assert created.status_code == 404
    assert created.json() == NOT_FOUND
    assert SOURCE not in created.text
    assert _rows(world) == []

    stored = VerifierBypassSubmission(
        id=uuid.uuid4(),
        verification_id=verification.id,
        submitter_user_id=REPORTER,
        state=BypassState.SUBMITTED.value,
        summary=SUMMARY,
        reproducer={"test_source": SOURCE, "failure_class": "integrity", "notes": NOTES},
        created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    world.bridge.sync.add(stored)
    world.bridge.sync.commit()

    hidden = world.client.get(f"{URL}/{stored.id}")
    missing = world.client.get(f"{URL}/{uuid.uuid4()}")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() == NOT_FOUND
    assert SOURCE not in hidden.text
    assert SUMMARY not in hidden.text

    _as(world, OPERATOR)
    decided = _decide(world, stored.id, "rejected")
    assert decided.status_code == 404
    assert decided.json() == NOT_FOUND
    assert stored.state == BypassState.SUBMITTED.value


@pytest.mark.parametrize("verdict", ["incomplete_fix_confirmed", "inconclusive", None, "Verified"])
def test_only_verified_verifications_accept_a_report(world, monkeypatch, verdict):
    _enable(monkeypatch)
    verification = _verification(world, verdict=verdict)
    response = _post(world, verification.id)
    assert response.status_code == 409
    assert response.json() == {"error": "not_verified"}
    assert _rows(world) == []

    missing = _post(world, uuid.uuid4())
    assert missing.status_code == 404
    assert missing.json() == NOT_FOUND


def test_verified_report_is_stored_for_the_submitter(world, monkeypatch):
    _enable(monkeypatch)
    verification = _verification(world, solver=str(SOLVER))
    response = _post(world, verification.id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "submitted"
    assert body["verification_id"] == str(verification.id)
    assert body["submitter_user_id"] == str(REPORTER)
    assert body["reproducer"]["test_source"] == SOURCE
    assert body["reproducer"]["failure_class"] == "integrity"
    assert body["reward_cents"] is None
    assert body["decided_at"] is None
    assert len(_rows(world)) == 1


def test_duplicate_open_submission_is_refused(world, monkeypatch):
    _enable(monkeypatch)
    verification = _verification(world, solver=str(SOLVER))
    first = _post(world, verification.id)
    assert first.status_code == 201, first.text
    duplicate = _post(world, verification.id)
    assert duplicate.status_code == 409
    assert duplicate.json() == {"error": "open_submission_exists"}
    assert SOURCE not in duplicate.text
    assert len(_rows(world)) == 1

    _as(world, STRANGER)
    other = _post(world, verification.id)
    assert other.status_code == 201, other.text
    assert len(_rows(world)) == 2

    _as(world, OPERATOR)
    rejected = _decide(world, first.json()["id"], "rejected", notes="not a bypass")
    assert rejected.status_code == 200, rejected.text
    _as(world, REPORTER)
    again = _post(world, verification.id)
    assert again.status_code == 201, again.text

    _as(world, OPERATOR)
    holding = _decide(world, again.json()["id"], "triaged")
    assert holding.status_code == 200, holding.text
    _as(world, REPORTER)
    while_open = _post(world, verification.id)
    assert while_open.status_code == 409
    assert while_open.json() == {"error": "open_submission_exists"}


def test_solver_self_report_is_refused(world, monkeypatch):
    _enable(monkeypatch)
    own = _verification(world, solver=str(REPORTER))
    refused = _post(world, own.id)
    assert refused.status_code == 403
    assert refused.json() == {"error": "solver_self_report"}
    assert SOURCE not in refused.text
    assert _rows(world) == []

    upper = _verification(world, solver=str(REPORTER).upper())
    assert _post(world, upper.id).status_code == 403

    _as(world, REPORTER, exchange_bot_id="bot-reporter")
    linked = _verification(world, solver="bot-reporter")
    assert _post(world, linked.id).status_code == 403
    assert _rows(world) == []

    _as(world, STRANGER)
    allowed = _post(world, own.id)
    assert allowed.status_code == 201, allowed.text


def test_visibility_is_submitter_or_operator_and_embargoes_the_reproducer(world, monkeypatch):
    _enable(monkeypatch)
    verification = _verification(world, solver=str(SOLVER))
    created = _post(world, verification.id)
    assert created.status_code == 201, created.text
    submission_id = created.json()["id"]

    owned = world.client.get(f"{URL}/{submission_id}")
    assert owned.status_code == 200
    assert owned.json()["reproducer"]["test_source"] == SOURCE

    _as(world, OPERATOR)
    operated = world.client.get(f"{URL}/{submission_id}")
    assert operated.status_code == 200
    assert operated.json()["reproducer"]["test_source"] == SOURCE
    assert operated.json()["summary"] == SUMMARY

    _as(world, STRANGER)
    hidden = world.client.get(f"{URL}/{submission_id}")
    missing = world.client.get(f"{URL}/{uuid.uuid4()}")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() == NOT_FOUND
    assert SOURCE not in hidden.text
    assert SUMMARY not in hidden.text
    assert NOTES not in hidden.text
    assert submission_id not in hidden.text


def test_decision_state_machine(world, monkeypatch):
    _enable(monkeypatch)
    verification = _verification(world, solver=str(SOLVER))
    created = _post(world, verification.id)
    submission_id = created.json()["id"]

    _as(world, OPERATOR)
    missing_reward = _decide(world, submission_id, "accepted")
    assert missing_reward.status_code == 400
    assert missing_reward.json() == {"error": "reward_required"}
    assert _rows(world)[0].state == "submitted"
    assert _rows(world)[0].decided_at is None

    negative = world.client.post(
        f"{URL}/{submission_id}/decision",
        json={"decision": "accepted", "reward_cents": -1},
    )
    assert negative.status_code == 422
    assert _rows(world)[0].state == "submitted"

    held = _decide(world, submission_id, "triaged", notes="queued for replay")
    assert held.status_code == 200, held.text
    assert held.json()["state"] == "triaged"
    assert held.json()["decided_at"] is None
    assert held.json()["decision_notes"] == "queued for replay"
    assert held.json()["reward_cents"] is None

    again = _decide(world, submission_id, "triaged")
    assert again.status_code == 409
    assert again.json() == {"error": "invalid_transition"}

    paid = _decide(world, submission_id, "accepted", reward=0, notes="replayed")
    assert paid.status_code == 200, paid.text
    assert paid.json()["state"] == "accepted"
    assert paid.json()["reward_cents"] == 0
    assert paid.json()["decided_at"] is not None
    assert paid.json()["decision_notes"] == "replayed"
    decided_at = paid.json()["decided_at"]

    blocked = _decide(world, submission_id, "rejected")
    assert blocked.status_code == 409
    assert blocked.json() == {"error": "invalid_transition"}
    assert _rows(world)[0].state == "accepted"
    assert _rows(world)[0].reward_cents == 0
    assert paid.json()["decided_at"] == decided_at

    direct = _post(world, verification.id)
    assert direct.status_code == 201, direct.text
    _as(world, OPERATOR)
    rejected = _decide(world, direct.json()["id"], "rejected", notes="already fixed")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["reward_cents"] is None
    assert rejected.json()["decided_at"] is not None
    reopened = _decide(world, direct.json()["id"], "accepted", reward=10)
    assert reopened.status_code == 409
    assert reopened.json() == {"error": "invalid_transition"}

    third = _post(world, _verification(world, solver=str(SOLVER)).id)
    assert third.status_code == 201, third.text
    _as(world, OPERATOR)
    straight = _decide(world, third.json()["id"], "accepted", reward=2500)
    assert straight.status_code == 200, straight.text
    assert straight.json()["state"] == "accepted"
    assert straight.json()["reward_cents"] == 2500
    assert straight.json()["decided_at"] is not None

    reward_on_reject = _post(world, _verification(world, solver=str(SOLVER)).id)
    _as(world, OPERATOR)
    refused = _decide(world, reward_on_reject.json()["id"], "rejected", reward=10)
    assert refused.status_code == 400
    assert refused.json() == {"error": "reward_not_allowed"}
    untouched = world.bridge.sync.get(
        VerifierBypassSubmission, uuid.UUID(reward_on_reject.json()["id"])
    )
    assert untouched is not None
    assert untouched.state == "submitted"
    assert untouched.reward_cents is None


def test_decisions_are_operator_only(world, monkeypatch):
    _enable(monkeypatch)
    verification = _verification(world, solver=str(SOLVER))
    created = _post(world, verification.id)
    submission_id = created.json()["id"]

    own = _decide(world, submission_id, "accepted", reward=100)
    assert own.status_code == 403
    assert own.json() == {"error": "forbidden"}
    assert SOURCE not in own.text

    _as(world, STRANGER)
    stranger = _decide(world, submission_id, "rejected")
    missing = _decide(world, uuid.uuid4(), "rejected")
    assert stranger.status_code == missing.status_code == 403
    assert stranger.json() == missing.json() == {"error": "forbidden"}
    assert SOURCE not in stranger.text
    assert _rows(world)[0].state == "submitted"

    _as(world, OPERATOR)
    assert _decide(world, uuid.uuid4(), "rejected").status_code == 404
    accepted = _decide(world, submission_id, "accepted", reward=100)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "accepted"


def test_accept_calls_the_reward_hook_without_moving_money(world, monkeypatch):
    _enable(monkeypatch)
    seen: list[int] = []
    original = record_verifier_bypass_reward

    async def spy(db, submission):
        seen.append(submission.reward_cents)
        await original(db, submission)

    monkeypatch.setattr("app.services.verifier_bypass.record_verifier_bypass_reward", spy)
    verification = _verification(world, solver=str(SOLVER))
    created = _post(world, verification.id)
    _as(world, OPERATOR)
    accepted = _decide(world, created.json()["id"], "accepted", reward=500)
    assert accepted.status_code == 200, accepted.text
    assert seen == [500]
    assert accepted.json()["reward_cents"] == 500
    assert _rows(world)[0].reward_cents == 500


def test_export_omits_reproducer_source(world, monkeypatch):
    _enable(monkeypatch)
    verification = _verification(world, solver=str(SOLVER))
    created = _post(world, verification.id)
    open_report = _post(world, _verification(world, solver=str(SOLVER)).id)
    assert open_report.status_code == 201
    _as(world, OPERATOR)
    accepted = _decide(world, created.json()["id"], "accepted", reward=800)
    assert accepted.status_code == 200, accepted.text
    rejected = _decide(world, open_report.json()["id"], "rejected")
    assert rejected.status_code == 200, rejected.text

    incomplete = VerifierBypassSubmission(
        id=uuid.uuid4(),
        verification_id=verification.id,
        submitter_user_id=STRANGER,
        state=BypassState.ACCEPTED.value,
        summary=SUMMARY,
        reproducer={"notes": NOTES},
        reward_cents=1,
        created_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
        decided_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
    )
    world.bridge.sync.add(incomplete)
    world.bridge.sync.commit()

    exported = _run(export_corpus_candidates(world.bridge))
    assert len(exported) == 1
    candidate = exported[0]
    assert candidate == {
        "submission_id": created.json()["id"],
        "verification_id": str(verification.id),
        "repo": REPO,
        "base_sha": verification.base_sha,
        "fix_sha": verification.fix_sha,
        "vuln_id": VULN,
        "failure_class": "integrity",
        "reproducer_sha256": _hash(SOURCE),
    }
    blob = json.dumps(exported)
    assert SOURCE not in blob
    assert NOTES not in blob
    assert SUMMARY not in blob
    assert "test_source" not in blob
    assert open_report.json()["id"] not in blob


def test_summary_and_reproducer_size_limits(world, monkeypatch):
    _enable(monkeypatch)
    verification = _verification(world, solver=str(SOLVER))
    too_long = _post(world, verification.id, summary="s" * (SUMMARY_MAX_LENGTH + 1))
    assert too_long.status_code == 422
    oversized = _body(verification.id)
    oversized["reproducer"] = {
        "test_source": "x" * (TEST_SOURCE_MAX_LENGTH + 1),
        "failure_class": "integrity",
        "notes": "",
    }
    assert world.client.post(URL, json=oversized).status_code == 422
    blank = _post(world, verification.id, summary="   ")
    assert blank.status_code == 400
    assert blank.json() == {"error": "invalid_summary"}
    assert _rows(world) == []

    payload = _body(verification.id, summary="s" * SUMMARY_MAX_LENGTH)
    payload["reproducer"]["failure_class"] = "not_a_class"
    assert world.client.post(URL, json=payload).status_code == 422
    assert _rows(world) == []
