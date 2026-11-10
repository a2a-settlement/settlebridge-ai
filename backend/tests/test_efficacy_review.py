"""Efficacy review on a partially approved submission. Services stubbed; no database."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.submission import SubmissionStatus
from app.routes import submissions as routes
from app.schemas.submission import EfficacyReviewRequest

ESCROW = "16d1a856-6e76-4187-ab51-121c2b640c38"


class _Db:
    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture()
def world(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), exchange_api_key="funder-key")
    sub = SimpleNamespace(
        id=uuid.uuid4(),
        bounty_id=uuid.uuid4(),
        agent_user_id=uuid.uuid4(),
        status=SubmissionStatus.PARTIALLY_APPROVED,
    )
    bounty = SimpleNamespace(
        id=sub.bounty_id, requester_id=user.id, title="Package", escrow_id=ESCROW
    )
    calls = {"exchange": [], "completed": [], "notified": [], "status_key": []}

    async def get_submission(db, submission_id):
        return sub

    async def get_bounty(db, bounty_id):
        return bounty

    async def complete(db, submission, *, efficacy_score, notes=None):
        calls["completed"].append({"score": efficacy_score, "notes": notes})

    async def notify(db, **kwargs):
        calls["notified"].append(kwargs)

    monkeypatch.setattr(routes.submission_service, "get_submission", get_submission)
    monkeypatch.setattr(routes.bounty_service, "get_bounty", get_bounty)
    monkeypatch.setattr(routes.submission_service, "complete_efficacy_review", complete)
    monkeypatch.setattr(routes, "create_notification", notify)
    monkeypatch.setattr(routes.SubmissionResponse, "model_validate", lambda obj: obj)
    monkeypatch.setattr(
        routes.exchange_svc,
        "release_escrow",
        lambda u, escrow_id: calls["exchange"].append(("release", escrow_id)),
    )
    monkeypatch.setattr(
        routes.exchange_svc,
        "refund_escrow",
        lambda u, escrow_id, reason="": calls["exchange"].append(("refund", escrow_id)),
    )

    def set_status(value):
        def fake(escrow_id, api_key=None):
            calls["status_key"].append(api_key)
            return value

        monkeypatch.setattr(routes.exchange_svc, "get_escrow_status", fake)

    return SimpleNamespace(user=user, sub=sub, calls=calls, set_status=set_status)


def _review(world, action: str, notes: str | None = None):
    body = EfficacyReviewRequest(score=40, action=action, notes=notes)
    return asyncio.run(routes.efficacy_review(world.sub.id, body, user=world.user, db=_Db()))


@pytest.mark.parametrize("action", ["release", "refund"])
def test_open_holdback_moves_funds(world, action):
    world.set_status("partially_released")
    _review(world, action)
    assert world.calls["exchange"] == [(action, ESCROW)]
    assert world.calls["status_key"] == ["funder-key"]
    assert world.calls["completed"] == [{"score": 40, "notes": None}]
    assert world.calls["notified"][0]["title"].startswith("Holdback ")


@pytest.mark.parametrize("action", ["release", "refund"])
def test_already_settled_holdback_records_review_without_moving_funds(world, action):
    world.set_status("released")
    _review(world, action, notes="missed")
    assert world.calls["exchange"] == []
    [completed] = world.calls["completed"]
    assert completed["notes"].startswith("missed Holdback was already settled")
    assert "escrow released" in completed["notes"]
    assert world.calls["notified"][0]["title"] == "Efficacy Review Recorded"


def test_unknown_status_falls_back_to_the_exchange_call(world, monkeypatch):
    world.set_status(None)

    def boom(u, escrow_id, reason=""):
        raise RuntimeError("400 Bad Request")

    monkeypatch.setattr(routes.exchange_svc, "refund_escrow", boom)
    with pytest.raises(HTTPException) as err:
        _review(world, "refund")
    assert err.value.status_code == 502
    assert world.calls["completed"] == []
