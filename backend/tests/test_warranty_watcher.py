"""Warranty watcher: forfeit, release, and operator review. No network."""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.fix_verifier import FixVerification, FixWarranty  # noqa: E402
from app.models.github import (  # noqa: E402
    FunderLedgerEntry,
    GitHubLease,
    GitHubSettlement,
)
from app.models.user import User, UserType  # noqa: E402
from app.services import warranty_watcher as watcher  # noqa: E402
from app.services.warranty_watcher import ReviewError, decide_review, run_once  # noqa: E402

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
REPO = "octo/hello"
FIX = "b" * 40
BASE = "a" * 40
ESCROW = "esc-warranty-1"
CANCEL = "settlement-cancel-secret"


class _Bridge:
    def __init__(self, sync: Session):
        self.sync = sync

    async def execute(self, statement, *args, **kwargs):
        return self.sync.execute(statement, *args, **kwargs)

    def add(self, obj):
        self.sync.add(obj)

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()


def _engine():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    models = (User, FunderLedgerEntry, GitHubSettlement, GitHubLease, FixVerification, FixWarranty)
    for model in models:
        model.__table__.create(engine)
    return engine


@pytest.fixture()
def db():
    engine = _engine()
    bridge = _Bridge(Session(engine, expire_on_commit=False))
    try:
        yield bridge
    finally:
        bridge.sync.close()
        engine.dispose()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _seed(
    db: _Bridge,
    *,
    state: str = "active",
    window_ends_at: datetime | None = None,
    files: list[str] | None = None,
    functions: list[str] | None = None,
    escrow_id: str | None = ESCROW,
    hold_secret: str | None = None,
):
    funder_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    verification_id = uuid.uuid4()
    warranty_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    funder = User(
        id=funder_id,
        email=f"funder-{funder_id.hex[:8]}@example.com",
        display_name="Funder",
        user_type=UserType.REQUESTER,
        exchange_api_key="ate_funder",
        exchange_bot_id="funder-bot",
    )
    verification = FixVerification(
        id=verification_id,
        repo_full_name=REPO,
        vuln_id=uuid.uuid4().hex,
        base_sha=BASE,
        fix_sha=FIX,
        state="done",
        verdict="verified",
        inputs={},
        result={
            "files": ["pkg/a.py"] if files is None else files,
            "functions": ["parse"] if functions is None else functions,
        },
        cost_cents=0,
        receipt_id="rcpt-1",
        embargo_state="private",
        solver_kind="unknown",
    )
    settlement = GitHubSettlement(
        id=uuid.uuid4(),
        binding_id=binding_id,
        spec_id=spec_id,
        solver_agent_id="solver-1",
        head_sha=FIX,
        state="released",
        escrow_id=escrow_id,
        cancel_secret=CANCEL,
        idempotency_prefix="gh-warranty",
    )
    ledger = FunderLedgerEntry(
        id=uuid.uuid4(),
        funder_user_id=funder_id,
        binding_id=binding_id,
        amount_ate=2500,
        status="settled",
    )
    warranty = FixWarranty(
        id=warranty_id,
        binding_id=binding_id,
        verification_id=verification_id,
        holdback_cents=500,
        window_ends_at=window_ends_at or (NOW + timedelta(days=30)),
        state=state,
        trigger_evidence={},
        created_at=NOW - timedelta(days=10),
    )
    db.add(funder)
    db.add(verification)
    db.add(settlement)
    db.add(ledger)
    db.add(warranty)
    if hold_secret is not None:
        db.add(
            GitHubLease(
                id=uuid.uuid4(),
                binding_id=binding_id,
                agent_id="solver-1",
                user_id=funder_id,
                github_user_id=7,
                spec_id=spec_id,
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                status="settled",
                work_expires_at=NOW,
                hard_expires_at=NOW,
                hold_escrow_id=escrow_id,
                hold_cancel_secret=hold_secret,
            )
        )
    asyncio.run(db.commit())
    return warranty


def _run(db, *, events=None, advisories=None, now=None, classify="false", hold_escrow=False):
    seen: list[tuple[str, list[str]]] = []

    def classify_revert(message, linked):
        seen.append((message, list(linked)))
        if callable(classify):
            return classify(message, linked)
        return classify

    calls: list[tuple] = []

    def get_escrow(user, escrow_id):
        calls.append(("status", escrow_id, user.id))
        return {"status": "partially_released"}

    def release_escrow(user, escrow_id, idempotency_key=None):
        calls.append(("release", escrow_id, idempotency_key, user.exchange_api_key))

    def refund_escrow(user, escrow_id, reason="", cancel_secret=None):
        calls.append(("refund", escrow_id, reason, cancel_secret, user.exchange_api_key))

    def forbid(*_args, **_kwargs):
        raise AssertionError("escrow must stay held")

    watcher.verifier_client.classify_revert = classify_revert
    if hold_escrow:
        watcher.exchange.get_escrow = forbid
        watcher.exchange.release_escrow = forbid
        watcher.exchange.refund_escrow = forbid
    else:
        watcher.exchange.get_escrow = get_escrow
        watcher.exchange.release_escrow = release_escrow
        watcher.exchange.refund_escrow = refund_escrow

    def git_events(repo, fix_sha):
        assert repo == REPO
        assert fix_sha == FIX
        return list(events or [])

    def advisory_source(repo, fix_sha):
        assert repo == REPO
        assert fix_sha == FIX
        return list(advisories or [])

    changed = asyncio.run(
        run_once(
            db,
            now=now or NOW,
            git_events_source=git_events,
            advisory_source=advisory_source,
        )
    )
    return changed, calls, seen


def _revert(message: str, *, sha: str = "d" * 40, linked=None, reverts: str | None = None):
    event = {"sha": sha, "message": message, "linked_texts": linked or ["linked-note"]}
    if reverts is not None:
        event["reverts"] = reverts
    return event


def _advisory(**overrides):
    body = {"id": "CVE-2026-1000", "files": ["pkg/a.py"], "functions": ["parse"]}
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Exchange stand-in so a review path cannot call the network."""

    def get_escrow(_user, _escrow_id):
        return {"status": "partially_released"}

    def release_escrow(_user, _escrow_id, idempotency_key=None):
        return {"status": "released"}

    def refund_escrow(_user, _escrow_id, reason="", cancel_secret=None):
        return {"status": "refunded"}

    monkeypatch.setattr(
        watcher.verifier_client,
        "classify_revert",
        watcher.verifier_client.classify_revert,
    )
    monkeypatch.setattr(watcher.exchange, "get_escrow", get_escrow)
    monkeypatch.setattr(watcher.exchange, "release_escrow", release_escrow)
    monkeypatch.setattr(watcher.exchange, "refund_escrow", refund_escrow)


def test_security_classification_forfeits_and_returns_the_holdback(db):
    warranty = _seed(db)
    changed, calls, seen = _run(
        db,
        events=[_revert("cleanup whitespace", linked=["GHSA-aaaa-bbbb-cccc"])],
        classify="true",
    )
    assert changed == 1
    assert warranty.state == "forfeited"
    assert warranty.trigger_kind == "security_revert"
    assert warranty.trigger_evidence["classification"] == "true"
    assert warranty.trigger_evidence["message"] == "cleanup whitespace"
    assert warranty.trigger_evidence["fix_sha"] == FIX
    assert seen == [("cleanup whitespace", ["GHSA-aaaa-bbbb-cccc"])]
    assert calls[0][0] == "status"
    refunds = [call for call in calls if call[0] == "refund"]
    assert len(refunds) == 1
    assert refunds[0][1] == ESCROW
    assert "security revert" in refunds[0][2]
    assert refunds[0][3] == CANCEL
    assert refunds[0][4] == "ate_funder"
    assert not any(call[0] == "release" for call in calls)
    assert _aware(warranty.resolved_at) == NOW


def test_adopted_hold_forfeit_uses_the_lease_cancel_secret(db):
    _seed(db, hold_secret="hold-secret")
    _changed, calls, _seen = _run(db, events=[_revert("anything")], classify="true")
    refunds = [call for call in calls if call[0] == "refund"]
    assert refunds[0][3] == "hold-secret"


@pytest.mark.parametrize(
    "message",
    ["Revert performance regression in the parser", "Revert build break on main"],
)
def test_non_security_classification_releases_the_holdback(db, message):
    warranty = _seed(db)
    _changed, calls, seen = _run(db, events=[_revert(message)], classify="false")
    assert warranty.state == "released"
    assert warranty.trigger_kind == "non_security_revert"
    assert seen[0][0] == message
    releases = [call for call in calls if call[0] == "release"]
    assert len(releases) == 1
    assert releases[0][1] == ESCROW
    assert releases[0][2] == f"warranty:{warranty.id}:release"
    assert releases[0][3] == "ate_funder"
    assert not any(call[0] == "refund" for call in calls)


def test_unknown_revert_holds_even_after_the_window(db, monkeypatch):
    monkeypatch.setattr(settings, "FIX_WARRANTY_REVIEW_SLA_DAYS", 7)
    warranty = _seed(db, window_ends_at=NOW - timedelta(days=1))
    changed, calls, _seen = _run(
        db,
        events=[_revert("Revert the fix")],
        classify="unknown",
        hold_escrow=True,
    )
    assert changed == 1
    assert warranty.state == "needs_review"
    assert calls == []


def test_unknown_classification_needs_review_and_holds_escrow(db, monkeypatch):
    monkeypatch.setattr(settings, "FIX_WARRANTY_REVIEW_SLA_DAYS", 3)
    warranty = _seed(db)
    changed, calls, _seen = _run(
        db, events=[_revert("Revert the fix")], classify="unknown", hold_escrow=True
    )
    assert changed == 1
    assert calls == []
    assert warranty.state == "needs_review"
    assert warranty.trigger_kind == "unknown_revert"
    assert warranty.trigger_evidence["classification"] == "unknown"
    assert warranty.review_decision is None
    assert warranty.resolved_at is None
    assert _aware(warranty.review_due_at) == NOW + timedelta(days=3)


def test_classifier_failure_or_unexpected_label_is_unknown(db, monkeypatch):
    monkeypatch.setattr(settings, "FIX_WARRANTY_REVIEW_SLA_DAYS", 7)
    warranty = _seed(db)

    def explode(_message, _linked):
        raise RuntimeError("classifier down")

    _run(db, events=[_revert("Revert the fix")], classify=explode)
    assert warranty.state == "needs_review"
    again = _seed(db)
    _run(db, events=[_revert("Revert the fix")], classify="TRUE")
    assert again.state == "needs_review"


def test_overlapping_followup_forfeits(db):
    warranty = _seed(db)
    changed, calls, _seen = _run(db, advisories=[_advisory()], classify="false")
    assert changed == 1
    assert warranty.state == "forfeited"
    assert warranty.trigger_kind == "followup_cve"
    assert warranty.trigger_evidence["advisory_id"] == "CVE-2026-1000"
    assert warranty.trigger_evidence["files"] == ["pkg/a.py"]
    assert warranty.trigger_evidence["functions"] == ["parse"]
    assert any(call[0] == "refund" for call in calls)
    assert not any(call[0] == "release" for call in calls)


def test_overlap_beats_a_non_security_or_unknown_revert(db):
    warranty = _seed(db)
    _run(
        db,
        events=[_revert("Revert performance regression")],
        advisories=[_advisory()],
        classify="false",
    )
    assert warranty.state == "forfeited"
    assert warranty.trigger_kind == "followup_cve"
    parked = _seed(db)
    _run(
        db,
        events=[_revert("Revert the fix")],
        advisories=[_advisory(id="CVE-2026-1001")],
        classify="unknown",
    )
    assert parked.state == "forfeited"
    assert parked.trigger_kind == "followup_cve"


def test_security_revert_beats_an_overlapping_advisory(db):
    warranty = _seed(db)
    _run(db, events=[_revert("cleanup")], advisories=[_advisory()], classify="true")
    assert warranty.trigger_kind == "security_revert"
    assert warranty.state == "forfeited"


@pytest.mark.parametrize(
    "advisory",
    [
        {"id": "CVE-2026-2000", "files": ["other.py"], "functions": ["parse"]},
        {"id": "CVE-2026-2001", "files": ["pkg/a.py"], "functions": ["dump"]},
    ],
)
def test_non_overlapping_advisory_does_not_forfeit(db, advisory):
    warranty = _seed(db)
    changed, calls, _seen = _run(db, advisories=[advisory])
    assert changed == 0
    assert warranty.state == "active"
    assert calls == []


def test_file_overlap_forfeits_when_one_side_names_no_functions(db):
    warranty = _seed(db, functions=[])
    _run(db, advisories=[{"id": "CVE-2026-3000", "files": ["pkg/a.py"], "functions": ["dump"]}])
    assert warranty.state == "forfeited"
    assert warranty.trigger_kind == "followup_cve"


def test_advisory_outside_the_window_is_ignored(db):
    warranty = _seed(db, window_ends_at=NOW + timedelta(days=5))
    late = _advisory(published_at=(NOW + timedelta(days=6)).isoformat())
    changed, calls, _seen = _run(db, advisories=[late])
    assert changed == 0
    assert warranty.state == "active"
    assert calls == []


def test_window_expiry_releases_when_nothing_triggered(db):
    warranty = _seed(db, window_ends_at=NOW)
    changed, calls, _seen = _run(
        db,
        advisories=[{"id": "CVE-2026-9", "files": ["unrelated.py"], "functions": []}],
    )
    assert changed == 1
    assert warranty.state == "released"
    assert warranty.trigger_kind == "window_elapsed"
    assert [call[0] for call in calls] == ["status", "release"]
    assert not any(call[0] == "refund" for call in calls)


def test_open_window_without_a_trigger_stays_active(db):
    warranty = _seed(db, window_ends_at=NOW + timedelta(seconds=1))
    changed, calls, _seen = _run(db, now=NOW)
    assert changed == 0
    assert warranty.state == "active"
    assert calls == []


def test_revert_of_a_different_sha_is_ignored(db):
    warranty = _seed(db)
    _changed, calls, seen = _run(db, events=[_revert("Revert security fix", reverts="e" * 40)])
    assert warranty.state == "active"
    assert seen == []
    assert calls == []


def test_rerun_does_not_move_money_twice(db):
    warranty = _seed(db)
    _run(db, events=[_revert("cleanup")], classify="true")
    assert warranty.state == "forfeited"
    changed, calls, _seen = _run(db, events=[_revert("cleanup")], classify="true")
    assert changed == 0
    assert warranty.state == "forfeited"
    assert [call for call in calls if call[0] == "refund"] == []


def test_released_warranty_is_not_forfeited_later(db):
    warranty = _seed(db, state="released", window_ends_at=NOW - timedelta(days=1))
    changed, calls, _seen = _run(db, events=[_revert("cleanup")], classify="true")
    assert changed == 0
    assert warranty.state == "released"
    assert calls == []


def test_needs_review_survives_the_window(db):
    warranty = _seed(db, state="needs_review", window_ends_at=NOW - timedelta(days=1))
    changed, calls, _seen = _run(db)
    assert changed == 0
    assert warranty.state == "needs_review"
    assert calls == []


def test_missing_escrow_does_not_forfeit(db):
    warranty = _seed(db, escrow_id=None)
    changed, calls, _seen = _run(db, events=[_revert("cleanup")], classify="true")
    assert changed == 0
    assert warranty.state == "active"
    assert calls == []


def test_source_outage_leaves_the_warranty_active(db):
    warranty = _seed(db, window_ends_at=NOW - timedelta(days=1))

    def boom(_repo, _sha):
        raise RuntimeError("github down")

    changed = asyncio.run(
        run_once(db, now=NOW, git_events_source=boom, advisory_source=lambda _r, _s: [])
    )
    assert changed == 0
    assert warranty.state == "active"


def test_operator_can_release_or_forfeit_a_review(db, monkeypatch):
    reviewer = uuid.uuid4()
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(reviewer))
    release_row = _seed(db, state="needs_review")
    result = asyncio.run(
        decide_review(db, release_row.id, decision="release", reviewer_id=reviewer, now=NOW)
    )
    assert result.state == "released"
    assert result.review_decision == "release"
    assert result.reviewed_by_user_id == reviewer
    assert _aware(result.resolved_at) == NOW
    stored = db.sync.execute(
        select(FixWarranty).where(FixWarranty.id == release_row.id)
    ).scalar_one()
    assert stored.review_decision == "release"

    forfeit_row = _seed(db, state="needs_review")
    asyncio.run(
        decide_review(db, forfeit_row.id, decision="forfeit", reviewer_id=reviewer, now=NOW)
    )
    assert forfeit_row.state == "forfeited"
    assert forfeit_row.review_decision == "forfeit"
    assert forfeit_row.reviewed_by_user_id == reviewer


def test_review_rejects_non_operators_and_other_states(db, monkeypatch):
    reviewer = uuid.uuid4()
    stranger = uuid.uuid4()
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(reviewer))
    waiting = _seed(db, state="needs_review")
    with pytest.raises(ReviewError):
        asyncio.run(
            decide_review(db, waiting.id, decision="forfeit", reviewer_id=stranger, now=NOW)
        )
    assert waiting.state == "needs_review"
    active = _seed(db, state="active")
    with pytest.raises(ReviewError):
        asyncio.run(
            decide_review(db, active.id, decision="release", reviewer_id=reviewer, now=NOW)
        )
    assert active.state == "active"
    asyncio.run(decide_review(db, waiting.id, decision="release", reviewer_id=reviewer, now=NOW))
    with pytest.raises(ReviewError):
        asyncio.run(
            decide_review(db, waiting.id, decision="forfeit", reviewer_id=reviewer, now=NOW)
        )
    assert waiting.state == "released"


def test_review_moves_money_once(db, monkeypatch):
    reviewer = uuid.uuid4()
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(reviewer))
    warranty = _seed(db, state="needs_review")
    calls: list[str] = []

    def get_escrow(_user, _escrow_id):
        return {"status": "partially_released"}

    def release_escrow(_user, escrow_id, idempotency_key=None):
        calls.append(f"release:{escrow_id}:{idempotency_key}")

    def refund_escrow(*_args, **_kwargs):
        calls.append("refund")

    watcher.exchange.get_escrow = get_escrow
    watcher.exchange.release_escrow = release_escrow
    watcher.exchange.refund_escrow = refund_escrow
    asyncio.run(decide_review(db, warranty.id, decision="release", reviewer_id=reviewer, now=NOW))
    assert calls == [f"release:{ESCROW}:warranty:{warranty.id}:release"]
