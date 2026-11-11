"""Fix-verifier orchestration. In-memory SQLite, verifier_client mocked, no network."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.fix_verifier import FixVerification, FixVerificationState, VerifierQuota
from app.models.github import GitHubRepoLink
from app.services import fix_verifier
from app.services.fix_verifier import (
    AdvisoryRequired,
    FeatureDisabled,
    NotOptedIn,
    request_verification,
    request_verification_receipt,
    supported_project,
)
from app.services.verifier_quota import QuotaExceeded
from tests.test_verifier_quota import INSTALLATION, REPO, _run, open_db

USER = SimpleNamespace(id=uuid.uuid4())
BASE = "a" * 40
FIX = "b" * 40
VULN = "GHSA-xxxx-yyyy-zzzz"
MARKER = "secret-repro-steps"


def _run_request(db, **overrides):
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
    kwargs = {
        "repo_full_name": REPO,
        "pr": 32,
        "base_sha": BASE,
        "fix_sha": FIX,
        "advisory": advisory,
        "alert_ref": None,
        "solver_kind": "house",
        "solver_principal_id": None,
        "user": USER,
    }
    kwargs.update(overrides)
    return request_verification(db, **kwargs)


@pytest.fixture()
def db():
    eng, bridge = open_db()
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 20)
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 10)
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_COST_CAP_CENTS", 2500)
    monkeypatch.setattr(settings, "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION", 500)


@pytest.fixture()
def link(db):
    row = GitHubRepoLink(
        id=uuid.uuid4(),
        installation_id=INSTALLATION,
        repo_full_name=REPO,
        enabled=True,
        fix_verifier_opt_in=True,
        default_policy={},
        required_checks=[],
        auto_dispatch_alerts=False,
        auto_merge_acceptable=False,
    )
    db.sync.add(row)
    db.sync.commit()
    return row


def _patch(monkeypatch, fn):
    monkeypatch.setattr(fix_verifier, "verify", fn)


def _quota(db):
    db.sync.expire_all()
    return db.sync.scalar(select(VerifierQuota))


def _count(db) -> int:
    db.sync.expire_all()
    return int(db.sync.scalar(select(func.count()).select_from(FixVerification)))


def test_supported_project_is_named_pypi_only():
    assert supported_project({"ecosystem": "  PyPI ", "package": "example-pkg"}) is True
    assert supported_project({"ecosystem": "pypi", "package": {"name": "example-pkg"}}) is True
    assert supported_project({"ecosystem": "npm", "package": "left-pad"}) is False
    assert supported_project({"ecosystem": "pip", "package": "example-pkg"}) is False
    assert supported_project({"ecosystem": "pypi", "package": "  "}) is False
    assert supported_project({"package": "example-pkg"}) is False
    assert supported_project("pypi") is False


def test_flag_off_raises_before_the_database(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", False)

    class Boom:
        async def execute(self, *args, **kwargs):
            raise AssertionError("database used while the feature is off")

    with pytest.raises(FeatureDisabled):
        _run(_run_request(Boom()))


def test_missing_disabled_and_not_opted_in_are_not_opted_in(db, enabled, link):
    with pytest.raises(NotOptedIn):
        _run(_run_request(db, repo_full_name="other/repo"))

    link.enabled = False
    db.sync.commit()
    with pytest.raises(NotOptedIn):
        _run(_run_request(db))

    link.enabled = True
    link.fix_verifier_opt_in = False
    db.sync.commit()
    with pytest.raises(NotOptedIn):
        _run(_run_request(db, advisory={}, alert_ref=None))
    assert _count(db) == 0


def test_alert_or_advisory_is_required_before_cache(db, enabled, link, monkeypatch):
    _patch(monkeypatch, lambda _request: {"verdict": "verified"})
    with pytest.raises(AdvisoryRequired):
        _run(_run_request(db, advisory={}, alert_ref=None))
    assert _count(db) == 0

    _run(
        _run_request(
            db,
            advisory={"vuln_id": VULN, "package": "example-pkg", "ecosystem": "pypi"},
            fix_sha="c" * 40,
        )
    )
    calls = {"n": 0}

    def verify_fn(_request):
        calls["n"] += 1
        return {"verdict": "verified"}

    _patch(monkeypatch, verify_fn)
    with pytest.raises(AdvisoryRequired):
        _run(_run_request(db, advisory={}, alert_ref="  ", fix_sha="c" * 40))
    assert calls["n"] == 0


def test_alert_ref_alone_is_enough_to_link_the_pr(db, enabled, link, monkeypatch):
    _patch(monkeypatch, lambda _request: (_ for _ in ()).throw(AssertionError("called")))
    row = _run(_run_request(db, advisory={}, alert_ref="dependabot/acme/widget/9"))
    assert row.vuln_id == "dependabot/acme/widget/9"
    assert row.alert_ref == "dependabot/acme/widget/9"
    assert row.verdict == "inconclusive"
    assert row.inconclusive_reason == "unsupported_project"
    assert row.cost_cents == 0
    assert _quota(db) is None


def test_cache_hit_skips_quota_and_the_client(db, enabled, link, monkeypatch):
    calls = []

    def verify_fn(request):
        calls.append(request)
        return {"verdict": "verified", "cost_cents": 15, "solver_kind": "third_party"}

    _patch(monkeypatch, verify_fn)
    first = _run(_run_request(db, repo_full_name="Acme/Widget"))
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 0)
    second = _run(_run_request(db, repo_full_name="ACME/widget", alert_ref="other-alert"))
    assert second.id == first.id
    assert len(calls) == 1
    assert first.repo_full_name == REPO
    assert first.solver_kind == "house"
    quota = _quota(db)
    assert quota.used == 1
    assert quota.spend_cents == 15
    assert _count(db) == 1


def test_unsupported_project_persists_before_any_spend(db, enabled, link, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 0)
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 0)
    calls = []
    _patch(monkeypatch, lambda request: calls.append(request) or {"verdict": "verified"})
    row = _run(
        _run_request(
            db,
            advisory={"vuln_id": VULN, "package": "left-pad", "ecosystem": "npm"},
        )
    )
    assert calls == []
    assert row.state == FixVerificationState.DONE.value
    assert row.verdict == "inconclusive"
    assert row.inconclusive_reason == "unsupported_project"
    assert row.result["detail"] == "launch scope is pure-Python PyPI only"
    assert row.cost_cents == 0
    assert row.embargo_state == "none"
    assert _quota(db) is None

    missing_package = _run(
        _run_request(
            db,
            fix_sha="d" * 40,
            advisory={"vuln_id": VULN, "ecosystem": "pypi", "package": ""},
        )
    )
    assert missing_package.inconclusive_reason == "unsupported_project"
    assert calls == []


def test_client_unsupported_refunds_the_slot(db, enabled, link, monkeypatch):
    _patch(
        monkeypatch,
        lambda _request: {"verdict": "inconclusive", "reason": "unsupported_project"},
    )
    row = _run(_run_request(db))
    assert row.inconclusive_reason == "unsupported_project"
    assert row.cost_cents == 0
    assert _quota(db).used == 0
    assert _quota(db).spend_cents == 0


def test_quota_and_rate_limit_do_not_call_the_client(db, enabled, link, monkeypatch):
    calls = []
    _patch(
        monkeypatch,
        lambda request: calls.append(request) or {"verdict": "verified", "cost_cents": 500},
    )
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 1)
    _run(_run_request(db))
    with pytest.raises(QuotaExceeded) as daily:
        _run(_run_request(db, fix_sha="c" * 40))
    assert daily.value.retry_after >= 1
    assert len(calls) == 1

    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_QUOTA", 20)
    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_COST_CAP_CENTS", 500)
    with pytest.raises(QuotaExceeded) as spend:
        _run(_run_request(db, fix_sha="d" * 40))
    assert spend.value.reason == "daily_cap"
    assert len(calls) == 1

    monkeypatch.setattr(settings, "FIX_VERIFIER_DAILY_COST_CAP_CENTS", 2500)
    monkeypatch.setattr(settings, "FIX_VERIFIER_RATE_LIMIT_PER_HOUR", 1)
    with pytest.raises(QuotaExceeded) as limited:
        _run(_run_request(db, fix_sha="e" * 40))
    assert limited.value.reason == "rate_limit"
    assert limited.value.retry_after >= 1
    assert len(calls) == 1
    assert _count(db) == 1


def test_success_persists_verdict_cost_and_passes_the_budget(db, enabled, link, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION", 123)
    seen = {}

    def verify_fn(request):
        seen["request"] = request
        return {
            "verdict": "verified",
            "reason": "controls_passed",
            "cost_cents": 99999,
            "solver_kind": "third_party",
            "detail": MARKER,
        }

    _patch(monkeypatch, verify_fn)
    row = _run(_run_request(db, solver_kind="house", solver_principal_id="alice"))
    assert row.state == FixVerificationState.DONE.value
    assert row.verdict == "verified"
    assert row.inconclusive_reason is None
    assert row.cost_cents == 123
    assert row.solver_kind == "house"
    assert row.solver_principal_id == "alice"
    assert row.result["detail"] == MARKER
    assert row.embargo_state == "none"
    assert row.created_by_user_id == USER.id
    assert seen["request"]["budget_cents"] == 123
    assert seen["request"]["solver_kind"] == "house"
    assert seen["request"]["repo"] == REPO
    assert seen["request"]["base_commit"] == BASE
    assert seen["request"]["fix_commit"] == FIX
    assert seen["request"]["advisory"]["package"] == "example-pkg"
    assert seen["request"]["solver_principal_sha256"] == hashlib.sha256(b"alice").hexdigest()
    assert "alice" not in seen["request"]["solver_principal_sha256"]
    quota = _quota(db)
    assert quota.used == 1
    assert quota.spend_cents == 123


def test_solver_kinds_pass_through(db, enabled, link, monkeypatch):
    for index, kind in enumerate(("house", "third_party", "unknown", "house-bot")):
        seen = {}

        def verify_fn(request, _seen=seen):
            _seen["kind"] = request["solver_kind"]
            return {"verdict": "verified", "solver_kind": "third_party", "cost_cents": 1}

        _patch(monkeypatch, verify_fn)
        row = _run(_run_request(db, solver_kind=kind, fix_sha=("f" * 39) + str(index)))
        expected = kind if kind in {"house", "third_party", "unknown"} else "unknown"
        assert seen["kind"] == expected
        assert row.solver_kind == expected


def test_client_exception_is_failed_and_does_not_echo_the_request(
    db, enabled, link, monkeypatch, caplog
):
    def explode(_request):
        raise RuntimeError(MARKER)

    _patch(monkeypatch, explode)
    with caplog.at_level(logging.DEBUG, logger="app.services.fix_verifier"):
        row = _run(_run_request(db))
    assert row.state == FixVerificationState.FAILED.value
    assert row.verdict == "inconclusive"
    assert row.inconclusive_reason == "infra_error"
    assert row.cost_cents == 0
    assert MARKER not in row.result["detail"]
    assert MARKER not in caplog.text
    assert REPO not in caplog.text
    quota = _quota(db)
    assert quota.used == 1
    assert quota.spend_cents == 0

    def verify_fn(_request):
        return {"verdict": "verified", "cost_cents": 4}

    _patch(monkeypatch, verify_fn)
    retried = _run(_run_request(db))
    assert retried.id == row.id
    assert retried.state == FixVerificationState.DONE.value
    assert retried.verdict == "verified"
    assert _quota(db).used == 2
    assert _quota(db).spend_cents == 4


def test_incomplete_fix_is_embargoed_private(db, enabled, link, monkeypatch):
    _patch(
        monkeypatch,
        lambda _request: {
            "verdict": "incomplete_fix_confirmed",
            "detail": MARKER,
            "cost_cents": 8,
        },
    )
    row = _run(_run_request(db))
    assert row.verdict == "incomplete_fix_confirmed"
    assert row.embargo_state == "private"
    assert row.state == FixVerificationState.DONE.value
    assert _quota(db).spend_cents == 8


def test_receipt_hook_is_called_for_a_new_done_row_and_not_a_cache_hit(
    db, enabled, link, monkeypatch
):
    calls = []
    real = fix_verifier.request_verification_receipt

    async def spy(db_session, verification):
        calls.append(verification.id)
        return await real(db_session, verification)

    monkeypatch.setattr(fix_verifier, "request_verification_receipt", spy)
    _patch(monkeypatch, lambda _request: {"verdict": "verified", "cost_cents": 1})
    row = _run(_run_request(db))
    _run(_run_request(db))
    assert calls == [row.id]
    assert asyncio.run(request_verification_receipt(db, row)) is None
    row.receipt_id = "  rcpt-9  "
    assert asyncio.run(request_verification_receipt(object(), row)) == "rcpt-9"


def test_replay_ignores_the_cache_and_spends_again(db, enabled, link, monkeypatch):
    calls = {"n": 0}

    def verify_fn(_request):
        calls["n"] += 1
        return {"verdict": "verified", "cost_cents": 5, "n": calls["n"]}

    _patch(monkeypatch, verify_fn)
    first = _run(_run_request(db))
    replayed = _run(fix_verifier.replay_verification(db, first, user=USER))
    assert replayed.id == first.id
    assert calls["n"] == 2
    assert replayed.result["n"] == 2
    assert replayed.solver_kind == "house"
    assert _quota(db).used == 2
    assert _quota(db).spend_cents == 10


def test_non_dict_client_result_is_a_failed_verification(db, enabled, link, monkeypatch):
    _patch(monkeypatch, lambda _request: "verified")
    row = _run(_run_request(db))
    assert row.state == FixVerificationState.FAILED.value
    assert row.verdict == "inconclusive"
    assert row.inconclusive_reason == "infra_error"
