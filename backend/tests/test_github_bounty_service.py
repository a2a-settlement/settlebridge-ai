"""GitHub bounty lifecycle: pure rules and handler behavior. No database and no network."""

# ruff: noqa: E402 — asyncpg is stubbed before the app import.

from __future__ import annotations

import httpx
import sys
import types

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.models.bounty import Bounty, BountyStatus
from app.models.github import (
    AcceptanceSpec,
    BindingState,
    FunderLedgerEntry,
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubJob,
    GitHubRepoLink,
    GitHubSettlement,
    JobKind,
    LedgerStatus,
)
from app.services import exchange, github_app, github_bounty_service as bounty_svc
from app.services import github_comments, github_settlement, mediator
from app.services.github_bounty_service import (
    BALANCE_NOTICE,
    REASON_CHECKS_NOT_PASSING,
    REASON_HOLD_EXPIRED,
    REASON_INCREASE_REJECTED,
    REASON_INSUFFICIENT,
    REASON_MERGED_SHA_MISMATCH,
    REASON_NO_LEASE_SUBMISSION,
    REASON_NOT_AWAITING_MERGE,
    REASON_SETTLEMENT_DISABLED,
    REASON_STALE_SPEC,
    REASON_UNLINKED,
    REASON_VIOLATION,
    ChecksResult,
    ParsedLabels,
    build_spec_content,
    effective_requirements,
    evaluate_required_checks,
    extract_test_command,
    handle_job,
    ledger_allows,
    merge_acceptance_decision,
    outcome_to_state,
    parse_bounty_labels,
    parse_closing_reference,
    spec_content_hash,
    usd_to_ate,
)
from app.services.github_errors import PermanentJobError, RetryableJobError

REPO = "acme/widget"
HEAD = "abc123def456"
BASE = "base000111"


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(side_effect=AssertionError("handler issued a query; patch a helper"))
    return db


def _job(kind, data, *, event="issues", action="labeled"):
    return GitHubJob(
        kind=kind.value if isinstance(kind, JobKind) else kind,
        payload={"event": event, "action": action, "data": data},
    )


def _run(db, job):
    asyncio.run(handle_job(db, job))


def _added(db):
    return [call.args[0] for call in db.add.call_args_list]


def _binding(**overrides) -> GitHubBountyBinding:
    binding = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=uuid.uuid4(),
        repo_full_name=REPO,
        issue_number=12,
        state=BindingState.ADVERTISED.value,
        pr_number=4,
        comment_id=5,
    )
    for key, value in overrides.items():
        setattr(binding, key, value)
    return binding


def _spec(binding_id, **overrides) -> AcceptanceSpec:
    spec = AcceptanceSpec(
        id=uuid.uuid4(),
        binding_id=binding_id,
        version=1,
        content_hash="hash-1",
        test_command="pytest -q",
        required_checks=[{"name": "ci", "app_id": 7}],
        policy={"sealed": True},
        payment_trigger="merge_with_checks",
        amount_ate=2500,
        fee_bps=1000,
        required_kya=1,
        required_ema=0.75,
    )
    for key, value in overrides.items():
        setattr(spec, key, value)
    return spec


def _patch_comments(monkeypatch):
    monkeypatch.setattr(github_comments, "bounty_marker", lambda bounty_id: f"<!--b:{bounty_id}-->")
    renders = {
        "render_advertised": MagicMock(return_value="advertised"),
        "render_not_advertised": MagicMock(return_value="not advertised"),
        "render_evaluation": MagicMock(return_value="evaluation"),
        "render_accepted_payment_pending": MagicMock(return_value="payment pending"),
        "render_not_payable": MagicMock(return_value="not payable"),
        "render_cancelled": MagicMock(return_value="cancelled"),
        "render_paid": MagicMock(return_value="paid"),
    }
    for name, mock in renders.items():
        monkeypatch.setattr(github_comments, name, mock)
    upsert = AsyncMock(return_value=77)
    monkeypatch.setattr(github_app, "upsert_sticky_comment", upsert)
    return renders, upsert


def _run_check(name="ci", app_id=7, head_sha=HEAD, **overrides):
    run = {
        "id": 1,
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": app_id},
        "completed_at": "2024-01-01T00:00:00Z",
        "started_at": "2024-01-01T00:00:00Z",
    }
    run.update(overrides)
    return run


# --- labels -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["bounty: $25"], ParsedLabels(Decimal("25"), None, False)),
        (["  Bounty: $25.50  "], ParsedLabels(Decimal("25.50"), None, False)),
        (["bounty:$25.5"], ParsedLabels(Decimal("25.5"), None, False)),
        (["bounty: $ 25"], ParsedLabels(Decimal("25"), None, False)),
        (["bounty: $10000"], ParsedLabels(Decimal("10000"), None, False)),
        (["bounty: $0.01"], ParsedLabels(Decimal("0.01"), None, False)),
        (["bounty: $25", "bounty: $25.00"], ParsedLabels(Decimal("25"), None, False)),
        (["tier: kya-0"], ParsedLabels(None, 0, False)),
        (["tier: KYA-5"], ParsedLabels(None, 5, False)),
        (["tier:kya-2"], ParsedLabels(None, 2, False)),
        (["tier: security"], ParsedLabels(None, None, True)),
        (["Tier: Security"], ParsedLabels(None, None, True)),
        (
            ["bounty: $25", "tier: kya-2", "tier: security"],
            ParsedLabels(Decimal("25"), 2, True),
        ),
        (["tier: kya-1", "tier: kya-4"], ParsedLabels(None, 4, False)),
        ([], ParsedLabels(None, None, False)),
        (["bug"], ParsedLabels(None, None, False)),
        (["bounty: $-5"], ParsedLabels(None, None, False)),
        (["bounty: $1e9"], ParsedLabels(None, None, False)),
        (["bounty: $1E2"], ParsedLabels(None, None, False)),
        (["bounty: 25"], ParsedLabels(None, None, False)),
        (["bounty: $25.555"], ParsedLabels(None, None, False)),
        (["bounty: $0"], ParsedLabels(None, None, False)),
        (["bounty: $0.00"], ParsedLabels(None, None, False)),
        (["bounty: $10000.01"], ParsedLabels(None, None, False)),
        (["bounty: $10,000"], ParsedLabels(None, None, False)),
        (["bounty: $25", "bounty: $30"], ParsedLabels(None, None, False)),
        (["bounty: $25", "bounty: $-5"], ParsedLabels(None, None, False)),
        (["tier: kya-6"], ParsedLabels(None, None, False)),
        (["tier: kya-2", "tier: security"], ParsedLabels(None, 2, True)),
    ],
)
def test_parse_bounty_labels(labels, expected):
    assert parse_bounty_labels(labels) == expected


# --- test command -----------------------------------------------------------


def test_extract_test_command_prefers_fence_after_reproduce():
    body = (
        "```\nnope\n```\n"
        "Steps to REPRODUCE the failure:\n"
        "```bash\npytest -q tests/test_a.py\n```\n"
        "```\nlater\n```\n"
    )
    assert extract_test_command(body) == "pytest -q tests/test_a.py"


def test_extract_test_command_falls_back_to_repro_line():
    assert extract_test_command("repro:   npm test  ") == "npm test"
    assert extract_test_command("Repro: pytest -q") == "pytest -q"


def test_extract_test_command_fence_wins_over_repro_line():
    body = "Please reproduce\n```\npytest -q\n```\nrepro: ignored\n"
    assert extract_test_command(body) == "pytest -q"


def test_extract_test_command_skips_empty_fence_and_caps_length():
    assert extract_test_command("reproduce\n```\n\n```\n```\npytest\n```\n") == "pytest"
    command = "y" * 600
    assert extract_test_command(f"reproduce\n```\n{command}\n```\n") == "y" * 500
    assert len(extract_test_command("repro: " + "z" * 800)) == 500


def test_extract_test_command_preserves_internal_newlines_and_rejects_empty():
    body = "How to reproduce:\n```\npytest -q \\\n  tests\n```\n"
    assert extract_test_command(body) == "pytest -q \\\n  tests"
    assert extract_test_command(None) is None
    assert extract_test_command("") is None
    assert extract_test_command("no instructions") is None
    assert extract_test_command("reproduce:\nno fence") is None


# --- money, requirements, hash, ledger --------------------------------------


def test_usd_to_ate_rounds_half_up(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_ATE_PER_USD", 100)
    assert usd_to_ate(Decimal("25")) == 2500
    assert usd_to_ate(Decimal("25.50")) == 2550
    assert usd_to_ate(Decimal("0.005")) == 1
    assert usd_to_ate(Decimal("0.004")) == 0
    monkeypatch.setattr(settings, "GITHUB_ATE_PER_USD", 1)
    assert usd_to_ate(Decimal("1.5")) == 2
    assert usd_to_ate(Decimal("2.5")) == 3


def test_effective_requirements_security_floor():
    plain = ParsedLabels(Decimal("25"), None, False)
    assert effective_requirements(plain, (1, 0.75)) == (1, 0.75)
    kya4 = ParsedLabels(Decimal("25"), 4, False)
    assert effective_requirements(kya4, (1, 0.75)) == (4, 0.75)
    secure = ParsedLabels(Decimal("25"), None, True)
    assert effective_requirements(secure, (1, 0.75)) == (2, 0.85)
    secure_high = ParsedLabels(Decimal("25"), 4, True)
    assert effective_requirements(secure_high, (1, 0.75)) == (4, 0.85)
    secure_low = ParsedLabels(Decimal("25"), 0, True)
    assert effective_requirements(secure_low, (3, 0.9)) == (2, 0.9)
    assert effective_requirements(secure, (3, 0.9)) == (3, 0.9)


def test_spec_hash_is_stable_and_key_order_independent():
    content = build_spec_content(
        test_command="pytest -q",
        required_checks=[{"app_id": 1, "name": "ci"}],
        policy={"max_files": 20},
        payment_trigger="merge_with_checks",
        amount_ate=2500,
        fee_bps=1000,
        required_kya=1,
        required_ema=0.75,
    )
    reordered = {
        "required_ema": 0.75,
        "policy": {"max_files": 20},
        "amount_ate": 2500,
        "test_command": "pytest -q",
        "fee_bps": 1000,
        "required_checks": [{"app_id": 1, "name": "ci"}],
        "payment_trigger": "merge_with_checks",
        "required_kya": 1,
    }
    digest = spec_content_hash(content)
    assert digest == spec_content_hash(reordered)
    assert digest == "a89821267e293d337d35f7eef17db11c393db3659858bc347e018df0500b6919"
    changed = dict(content)
    changed["amount_ate"] = 2501
    assert spec_content_hash(changed) != digest
    nested = build_spec_content(
        test_command=None,
        required_checks=[],
        policy={"a": {"d": 1, "b": 2}, "z": 1},
        payment_trigger="merge_with_checks",
        amount_ate=1,
        fee_bps=1,
        required_kya=0,
        required_ema=0.5,
    )
    swapped = dict(nested)
    swapped["policy"] = {"z": 1, "a": {"b": 2, "d": 1}}
    assert spec_content_hash(nested) == spec_content_hash(swapped)


def test_ledger_allows_stops_at_the_checked_balance():
    assert ledger_allows(0, 0, 0) is True
    assert ledger_allows(100, 50, 150) is True
    assert ledger_allows(100, 51, 150) is False
    assert ledger_allows(80, 25, 100) is False
    assert ledger_allows(0, 1, 0) is False


# --- required checks --------------------------------------------------------


def test_required_checks_ignore_other_sha_and_other_app():
    required = [{"name": "ci", "app_id": 7}]
    runs = [
        _run_check(head_sha="other-sha"),
        _run_check(app_id=99),
        _run_check(name="lint"),
    ]
    assert evaluate_required_checks(required, runs, HEAD) == ChecksResult("pending", [])


def test_required_checks_pending_failed_passed_and_unconfigured():
    required = [{"name": "ci", "app_id": 7}, {"name": "lint", "app_id": 7}]
    assert evaluate_required_checks([], [_run_check()], HEAD) == ChecksResult("unconfigured", [])
    pending = [
        _run_check(status="in_progress", conclusion=None),
        _run_check(name="lint"),
    ]
    assert evaluate_required_checks(required, pending, HEAD) == ChecksResult("pending", [])
    failed = [
        _run_check(conclusion="failure"),
        _run_check(name="lint", status="queued", conclusion=None),
    ]
    assert evaluate_required_checks(required, failed, HEAD) == ChecksResult("failed", ["ci"])
    skipped = [_run_check(conclusion="skipped"), _run_check(name="lint")]
    assert evaluate_required_checks(required, skipped, HEAD) == ChecksResult("failed", ["ci"])
    passed = [_run_check(), _run_check(name="lint")]
    assert evaluate_required_checks(required, passed, HEAD) == ChecksResult("passed", [])
    both = [
        _run_check(conclusion="failure"),
        _run_check(name="lint", conclusion="timed_out"),
    ]
    assert evaluate_required_checks(required, both, HEAD).failed == ["ci", "lint"]


def test_required_checks_use_the_latest_matching_run():
    required = [{"name": "ci", "app_id": 7}]
    older_fail = _run_check(conclusion="failure", completed_at="2020-01-01T00:00:00Z", id=1)
    newer_ok = _run_check(completed_at="2021-01-01T00:00:00Z", id=2)
    assert evaluate_required_checks(required, [older_fail, newer_ok], HEAD).state == "passed"
    newer_fail = _run_check(conclusion="failure", completed_at="2022-01-01T00:00:00Z", id=3)
    assert evaluate_required_checks(required, [newer_ok, newer_fail], HEAD).state == "failed"
    same_time = [
        _run_check(
            conclusion="failure",
            completed_at="2021-01-01T00:00:00Z",
            started_at="2021-01-01T00:00:01Z",
            id=4,
        ),
        _run_check(
            completed_at="2021-01-01T00:00:00Z",
            started_at="2021-01-01T00:00:02Z",
            id=5,
        ),
    ]
    assert evaluate_required_checks(required, same_time, HEAD).state == "passed"
    by_id = [
        _run_check(
            conclusion="failure",
            completed_at="2021-01-01T00:00:00Z",
            started_at="2021-01-01T00:00:00Z",
            id=8,
        ),
        _run_check(
            completed_at="2021-01-01T00:00:00Z",
            started_at="2021-01-01T00:00:00Z",
            id=9,
        ),
    ]
    assert evaluate_required_checks(required, by_id, HEAD).state == "passed"
    in_progress = _run_check(
        status="in_progress",
        conclusion=None,
        completed_at=None,
        started_at="2023-01-01T00:00:00Z",
        id=11,
    )
    assert evaluate_required_checks(required, [newer_ok, in_progress], HEAD).state == "pending"


# --- merge decision and closing references ----------------------------------


def test_outcome_to_state():
    assert outcome_to_state("ACCEPTABLE") == BindingState.AWAITING_MERGE
    assert outcome_to_state("NEEDS_REVIEW") == BindingState.NEEDS_REVIEW
    assert outcome_to_state("POLICY_VIOLATION") == BindingState.ADVERTISED
    assert outcome_to_state("REJECTED_TOO_LARGE") == BindingState.ADVERTISED
    assert outcome_to_state("REJECTED_CHECKS") == BindingState.ADVERTISED
    assert outcome_to_state("NOPE") == BindingState.ADVERTISED


def _awaiting(**overrides):
    spec_id = uuid.uuid4()
    binding = SimpleNamespace(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=spec_id,
        evaluated_spec_id=spec_id,
        last_assessment={"findings": []},
        pr_number=4,
    )
    for key, value in overrides.items():
        setattr(binding, key, value)
    return binding


def _submitted_lease(binding, **overrides):
    lease = SimpleNamespace(
        status="submitted",
        pr_number=getattr(binding, "pr_number", 4),
        spec_id=binding.evaluated_spec_id,
        agent_id="agent-7",
        user_id=uuid.uuid4(),
        release_reason=None,
    )
    for key, value in overrides.items():
        setattr(lease, key, value)
    return lease


def test_merge_acceptance_decision():
    binding = _awaiting()
    ok, reason = merge_acceptance_decision(binding, HEAD, _submitted_lease(binding))
    assert ok is True and reason == "accepted"
    drifted = _awaiting(current_spec_id=uuid.uuid4())
    assert merge_acceptance_decision(drifted, HEAD, _submitted_lease(drifted))[0] is True
    review = _awaiting(last_assessment={"findings": [{"severity": "review", "rule": "style"}]})
    assert merge_acceptance_decision(review, HEAD, _submitted_lease(review))[0] is True
    wrong = _awaiting()
    assert merge_acceptance_decision(wrong, "other", _submitted_lease(wrong)) == (
        False,
        REASON_MERGED_SHA_MISMATCH,
    )
    stale = _awaiting()
    assert merge_acceptance_decision(
        stale, HEAD, _submitted_lease(stale, spec_id=uuid.uuid4())
    ) == (False, REASON_STALE_SPEC)
    violation = _awaiting(
        last_assessment={"findings": [{"severity": "violation", "rule": "secrets"}]}
    )
    assert merge_acceptance_decision(violation, HEAD, _submitted_lease(violation)) == (
        False,
        REASON_VIOLATION,
    )
    needs = _awaiting(state=BindingState.NEEDS_REVIEW.value)
    assert merge_acceptance_decision(needs, HEAD, _submitted_lease(needs)) == (
        False,
        REASON_NOT_AWAITING_MERGE,
    )
    bare = _awaiting()
    assert merge_acceptance_decision(bare, HEAD, None) == (False, REASON_NO_LEASE_SUBMISSION)
    assert merge_acceptance_decision(
        bare, HEAD, _submitted_lease(bare, status="released")
    ) == (False, REASON_NO_LEASE_SUBMISSION)
    assert merge_acceptance_decision(
        bare, HEAD, _submitted_lease(bare, pr_number=9)
    ) == (False, REASON_NO_LEASE_SUBMISSION)
    fresh = _awaiting()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert merge_acceptance_decision(
        fresh, HEAD, _submitted_lease(fresh, hold_expires_at=None)
    )[0] is True
    assert merge_acceptance_decision(
        fresh, HEAD, _submitted_lease(fresh, hold_expires_at=future)
    )[0] is True
    naive_future = datetime(2099, 1, 1)
    assert merge_acceptance_decision(
        fresh, HEAD, _submitted_lease(fresh, hold_expires_at=naive_future)
    )[0] is True
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert merge_acceptance_decision(
        fresh, HEAD, _submitted_lease(fresh, hold_expires_at=expired)
    ) == (False, REASON_HOLD_EXPIRED)
    naive_past = datetime(2020, 1, 1)
    assert merge_acceptance_decision(
        fresh, HEAD, _submitted_lease(fresh, hold_expires_at=naive_past)
    ) == (False, REASON_HOLD_EXPIRED)
    eastern = timezone(timedelta(hours=-5))
    aware_past = datetime.now(eastern) - timedelta(minutes=5)
    assert merge_acceptance_decision(
        fresh, HEAD, _submitted_lease(fresh, hold_expires_at=aware_past)
    ) == (False, REASON_HOLD_EXPIRED)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Fixes #12", 12),
        ("this closes #3.", 3),
        ("RESOLVES #8", 8),
        ("Fixes: #15", 15),
        (f"Closes {REPO}#9", 9),
        ("Resolves ACME/Widget#4", 4),
        ("Fixes other/repo#9", None),
        ("Fixes other/repo#1 and Closes #6", 6),
        ("Fixes #12 and Closes #13", 12),
        ("prefixfixes #1", None),
        ("Fix #3", None),
        ("See #3", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_closing_reference(body, expected):
    assert parse_closing_reference(body, REPO) == expected


# --- handlers ---------------------------------------------------------------


def _issue_data(labels, body="Please reproduce\n```\npytest -q\n```\n", number=12):
    return {
        "installation": {"id": 42},
        "repository": {"full_name": REPO},
        "issue": {"number": number, "title": "Fix the widget", "body": body, "labels": labels},
    }


def _link(user_id):
    return SimpleNamespace(
        id=uuid.uuid4(),
        installation_id=42,
        repo_full_name=REPO,
        funder_user_id=user_id,
        enabled=True,
        default_policy={"deny_paths": ["secrets/"]},
        required_checks=[{"name": "ci", "app_id": 7}],
    )


def test_issue_labeled_advertises_within_checked_balance(monkeypatch):
    renders, upsert = _patch_comments(monkeypatch)
    user = SimpleNamespace(id=uuid.uuid4(), exchange_bot_id="bot-1")
    link = _link(user.id)
    db = _db()
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_enabled_funded_repo_link",
        AsyncMock(return_value=link),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.lock_user", AsyncMock(return_value=user)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.sum_advertised_amount", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        exchange, "get_balance_dashboard", MagicMock(return_value={"available": 1_000_000})
    )
    labels = [{"name": "bounty: $25"}, {"name": "tier: security"}]
    _run(db, _job(JobKind.ISSUE_LABELED, _issue_data(labels)))

    added = _added(db)
    bounties = [row for row in added if isinstance(row, Bounty)]
    bindings = [row for row in added if isinstance(row, GitHubBountyBinding)]
    specs = [row for row in added if isinstance(row, AcceptanceSpec)]
    ledgers = [row for row in added if isinstance(row, FunderLedgerEntry)]
    assert len(bounties) == len(bindings) == len(specs) == len(ledgers) == 1
    bounty, binding, spec, ledger = bounties[0], bindings[0], specs[0], ledgers[0]
    assert bounty.status == BountyStatus.OPEN
    assert bounty.escrow_id == "pending_claim"
    assert bounty.requester_id == user.id
    assert bounty.tags == ["github", REPO]
    assert bounty.reward_amount == 2500
    assert bounty.min_reputation == 0.85
    assert binding.bounty_id == bounty.id
    assert binding.state == BindingState.ADVERTISED.value
    assert binding.current_spec_id == spec.id
    assert binding.comment_id == 77
    assert spec.version == 1
    assert spec.payment_trigger == "merge_with_checks"
    assert spec.required_kya == 2
    assert spec.required_ema == 0.85
    assert spec.amount_ate == 2500
    assert spec.test_command == "pytest -q"
    assert spec.policy == {"deny_paths": ["secrets/"]}
    assert spec.policy is not link.default_policy
    assert spec.required_checks == [{"name": "ci", "app_id": 7}]
    assert spec.content_hash == spec_content_hash(
        build_spec_content(
            test_command="pytest -q",
            required_checks=[{"name": "ci", "app_id": 7}],
            policy={"deny_paths": ["secrets/"]},
            payment_trigger="merge_with_checks",
            amount_ate=2500,
            fee_bps=settings.GITHUB_CLEARING_FEE_BPS,
            required_kya=2,
            required_ema=0.85,
        )
    )
    assert ledger.binding_id == binding.id
    assert ledger.funder_user_id == user.id
    assert ledger.amount_ate == 2500
    assert ledger.status == LedgerStatus.ADVERTISED.value
    assert BALANCE_NOTICE in upsert.await_args.args[4]
    assert renders["render_advertised"].call_args.kwargs["amount_ate"] == 2500
    assert renders["render_advertised"].call_args.kwargs["spec_version"] == 1
    assert renders["render_advertised"].call_args.kwargs["market_url"] == settings.MARKET_URL
    exchange.get_balance_dashboard.assert_called_once_with("bot-1")


def test_issue_labeled_identical_hash_does_not_create_a_version(monkeypatch):
    _patch_comments(monkeypatch)
    user_id = uuid.uuid4()
    link = _link(user_id)
    binding = _binding()
    content = build_spec_content(
        test_command="pytest -q",
        required_checks=[{"name": "ci", "app_id": 7}],
        policy={"deny_paths": ["secrets/"]},
        payment_trigger="merge_with_checks",
        amount_ate=2500,
        fee_bps=settings.GITHUB_CLEARING_FEE_BPS,
        required_kya=1,
        required_ema=settings.GITHUB_DEFAULT_REQUIRED_EMA,
    )
    spec = _spec(binding.id, version=4, content_hash=spec_content_hash(content), amount_ate=2500)
    binding.current_spec_id = spec.id
    binding.comment_id = 9
    db = _db()
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=binding)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_enabled_funded_repo_link",
        AsyncMock(return_value=link),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_acceptance_spec", AsyncMock(return_value=spec)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_active_lease", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_ledger_entry", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.lock_user",
        AsyncMock(side_effect=AssertionError("balance lock on identical spec")),
    )
    monkeypatch.setattr(
        exchange, "get_balance_dashboard", MagicMock(side_effect=AssertionError("balance call"))
    )
    _run(db, _job(JobKind.ISSUE_CHANGED, _issue_data([{"name": "bounty: $25"}]), action="edited"))
    assert db.add.call_count == 0
    assert spec.version == 4
    assert binding.current_spec_id == spec.id
    assert github_app.upsert_sticky_comment.await_args.kwargs["comment_id"] == 9


def test_spec_edit_inserts_a_new_version_and_rejects_overcommit(monkeypatch):
    renders, _upsert = _patch_comments(monkeypatch)
    user = SimpleNamespace(id=uuid.uuid4(), exchange_bot_id="bot-1")
    link = _link(user.id)
    binding = _binding()
    current = _spec(binding.id, version=1, content_hash="stale-hash", amount_ate=2500)
    binding.current_spec_id = current.id
    ledger = SimpleNamespace(
        amount_ate=2500, status=LedgerStatus.ADVERTISED.value, funder_user_id=user.id
    )
    bounty = SimpleNamespace(reward_amount=2500, min_reputation=0.75, status=BountyStatus.OPEN)
    active_lease = SimpleNamespace(spec_id=current.id, status="submitted", pr_number=4)
    db = _db()
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=binding)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_active_lease",
        AsyncMock(return_value=active_lease),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_enabled_funded_repo_link",
        AsyncMock(return_value=link),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_acceptance_spec", AsyncMock(return_value=current)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.lock_user", AsyncMock(return_value=user)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_ledger_entry", AsyncMock(return_value=ledger)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.sum_advertised_amount", AsyncMock(return_value=2500)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_bounty", AsyncMock(return_value=bounty)
    )
    monkeypatch.setattr(
        exchange, "get_balance_dashboard", MagicMock(return_value={"available": 2900})
    )
    _run(db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $30"}])))
    assert not any(isinstance(row, AcceptanceSpec) for row in _added(db))
    assert current.version == 1
    assert current.amount_ate == 2500
    assert current.content_hash == "stale-hash"
    assert ledger.amount_ate == 2500
    assert bounty.reward_amount == 2500
    assert REASON_INCREASE_REJECTED == renders["render_not_advertised"].call_args.kwargs["reason"]

    exchange.get_balance_dashboard.return_value = {"available": 100_000}
    _run(db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $30"}])))
    specs = [row for row in _added(db) if isinstance(row, AcceptanceSpec)]
    assert len(specs) == 1
    assert specs[0].version == 2
    assert specs[0].amount_ate == 3000
    assert specs[0] is not current
    assert current.version == 1 and current.content_hash == "stale-hash"
    assert binding.current_spec_id == specs[0].id
    assert active_lease.spec_id == current.id
    assert active_lease.spec_id != binding.current_spec_id
    assert ledger.amount_ate == 3000
    assert bounty.reward_amount == 3000
    assert not any(isinstance(row, FunderLedgerEntry) for row in _added(db))


def _prepare_terms(
    monkeypatch,
    *,
    ledger_amount,
    spec_amount,
    lease_status,
    link_funder_id=None,
    balance=100_000,
    content_hash="stale-hash",
):
    renders, _upsert = _patch_comments(monkeypatch)
    user = SimpleNamespace(id=uuid.uuid4(), exchange_bot_id="bot-original")
    link = _link(user.id if link_funder_id is None else link_funder_id)
    binding = _binding()
    current = _spec(binding.id, version=1, content_hash=content_hash, amount_ate=spec_amount)
    binding.current_spec_id = current.id
    ledger = SimpleNamespace(
        amount_ate=ledger_amount,
        status=LedgerStatus.ADVERTISED.value,
        funder_user_id=user.id,
    )
    bounty = SimpleNamespace(
        reward_amount=ledger_amount,
        min_reputation=0.75,
        status=BountyStatus.OPEN,
        requester_id=user.id,
    )
    active = None
    if lease_status is not None:
        active = SimpleNamespace(spec_id=current.id, status=lease_status, pr_number=4)
    locked = []
    events = []
    available = {"value": balance}

    async def _lock(_db, user_id):
        events.append("lock")
        locked.append(user_id)
        if user_id != user.id:
            raise AssertionError("locked a funder other than the ledger funder")
        return user

    def _dashboard(account_id):
        events.append(("balance", account_id))
        return {"available": available["value"]}

    db = _db()
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=binding)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_enabled_funded_repo_link",
        AsyncMock(return_value=link),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_acceptance_spec", AsyncMock(return_value=current)
    )
    monkeypatch.setattr("app.services.github_bounty_service.lock_user", _lock)
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_ledger_entry", AsyncMock(return_value=ledger)
    )
    summed = AsyncMock(return_value=ledger_amount)
    monkeypatch.setattr("app.services.github_bounty_service.sum_advertised_amount", summed)
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_bounty", AsyncMock(return_value=bounty)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_active_lease", AsyncMock(return_value=active)
    )
    dashboard = MagicMock(side_effect=_dashboard)
    monkeypatch.setattr(exchange, "get_balance_dashboard", dashboard)
    return SimpleNamespace(
        db=db,
        user=user,
        link=link,
        binding=binding,
        current=current,
        ledger=ledger,
        bounty=bounty,
        locked=locked,
        events=events,
        available=available,
        summed=summed,
        dashboard=dashboard,
        renders=renders,
    )


@pytest.mark.parametrize("lease_status", ["work", "submitted"])
def test_label_decrease_with_active_lease_keeps_pinned_amount(monkeypatch, lease_status):
    world = _prepare_terms(
        monkeypatch, ledger_amount=2500, spec_amount=2500, lease_status=lease_status
    )
    _run(world.db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $10"}])))
    label_amount = usd_to_ate(Decimal("10"))
    specs = [row for row in _added(world.db) if isinstance(row, AcceptanceSpec)]
    assert len(specs) == 1
    assert specs[0].amount_ate == label_amount
    assert world.ledger.amount_ate == 2500
    assert world.bounty.reward_amount == 2500
    assert world.current.amount_ate == 2500
    world.dashboard.assert_not_called()


def test_label_decrease_without_a_lease_lowers_the_ledger(monkeypatch):
    world = _prepare_terms(monkeypatch, ledger_amount=2500, spec_amount=2500, lease_status=None)
    _run(world.db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $10"}])))
    label_amount = usd_to_ate(Decimal("10"))
    specs = [row for row in _added(world.db) if isinstance(row, AcceptanceSpec)]
    assert len(specs) == 1
    assert specs[0].amount_ate == label_amount
    assert world.ledger.amount_ate == label_amount
    assert world.bounty.reward_amount == label_amount
    world.dashboard.assert_not_called()


def test_lease_floor_increase_is_balance_checked_under_the_funder_lock(monkeypatch):
    world = _prepare_terms(
        monkeypatch, ledger_amount=1000, spec_amount=2500, lease_status="submitted", balance=2000
    )
    job = _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $10"}]))
    _run(world.db, job)
    assert world.ledger.amount_ate == 1000
    assert world.bounty.reward_amount == 1000
    assert not any(isinstance(row, AcceptanceSpec) for row in _added(world.db))
    assert world.renders["render_not_advertised"].call_args.kwargs["reason"] == (
        REASON_INCREASE_REJECTED
    )
    assert world.events[:2] == ["lock", ("balance", "bot-original")]
    assert world.summed.await_args.args[1] == world.user.id

    world.available["value"] = 100_000
    _run(world.db, job)
    label_amount = usd_to_ate(Decimal("10"))
    specs = [row for row in _added(world.db) if isinstance(row, AcceptanceSpec)]
    assert len(specs) == 1
    assert specs[0].amount_ate == label_amount
    assert world.ledger.amount_ate == 2500
    assert world.bounty.reward_amount == 2500
    assert world.locked == [world.user.id, world.user.id]


def test_terms_update_locks_the_ledger_funder_not_the_repo_link(monkeypatch):
    replacement = uuid.uuid4()
    world = _prepare_terms(
        monkeypatch,
        ledger_amount=2500,
        spec_amount=2500,
        lease_status=None,
        link_funder_id=replacement,
    )
    assert world.link.funder_user_id == replacement
    _run(world.db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $30"}])))
    assert world.locked == [world.user.id]
    assert world.events[:2] == ["lock", ("balance", "bot-original")]
    assert world.summed.await_args.args[1] == world.user.id
    assert world.ledger.funder_user_id == world.user.id
    assert world.bounty.requester_id == world.user.id
    assert world.ledger.amount_ate == 3000
    world.dashboard.assert_called_once_with("bot-original")


def test_unchanged_terms_release_the_lease_floor_only_when_unleased(monkeypatch):
    content = build_spec_content(
        test_command="pytest -q",
        required_checks=[{"name": "ci", "app_id": 7}],
        policy={"deny_paths": ["secrets/"]},
        payment_trigger="merge_with_checks",
        amount_ate=usd_to_ate(Decimal("10")),
        fee_bps=settings.GITHUB_CLEARING_FEE_BPS,
        required_kya=1,
        required_ema=settings.GITHUB_DEFAULT_REQUIRED_EMA,
    )
    content_hash = spec_content_hash(content)
    held = _prepare_terms(
        monkeypatch,
        ledger_amount=2500,
        spec_amount=1000,
        lease_status="submitted",
        content_hash=content_hash,
    )
    job = _job(JobKind.ISSUE_CHANGED, _issue_data([{"name": "bounty: $10"}]), action="edited")
    _run(held.db, job)
    assert held.ledger.amount_ate == 2500
    assert held.bounty.reward_amount == 2500
    assert held.db.add.call_count == 0
    assert held.locked == []

    released = _prepare_terms(
        monkeypatch,
        ledger_amount=2500,
        spec_amount=1000,
        lease_status=None,
        content_hash=content_hash,
    )
    _run(released.db, job)
    assert released.ledger.amount_ate == 1000
    assert released.bounty.reward_amount == 1000
    assert released.db.add.call_count == 0
    assert released.locked == []


def test_insufficient_new_bounty_does_not_insert_rows(monkeypatch):
    renders, upsert = _patch_comments(monkeypatch)
    user = SimpleNamespace(id=uuid.uuid4(), exchange_bot_id="bot-1")
    db = _db()
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_enabled_funded_repo_link",
        AsyncMock(return_value=_link(user.id)),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.lock_user", AsyncMock(return_value=user)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.sum_advertised_amount", AsyncMock(return_value=100)
    )
    monkeypatch.setattr(
        exchange, "get_balance_dashboard", MagicMock(return_value={"available": 100})
    )
    _run(db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $25"}])))
    assert db.add.call_count == 0
    assert renders["render_not_advertised"].call_args.kwargs["reason"] == REASON_INSUFFICIENT
    assert BALANCE_NOTICE in upsert.await_args.args[4]
    assert upsert.await_args.args[3].startswith("<!-- settlebridge:repo:")


def test_unlinked_repo_is_not_advertised(monkeypatch):
    renders, _upsert = _patch_comments(monkeypatch)
    db = _db()
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_enabled_funded_repo_link",
        AsyncMock(return_value=None),
    )
    _run(db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bounty: $25"}])))
    assert db.add.call_count == 0
    assert renders["render_not_advertised"].call_args.kwargs["reason"] == REASON_UNLINKED


def test_removing_the_bounty_label_cancels_without_touching_settlement(monkeypatch):
    renders, _upsert = _patch_comments(monkeypatch)
    binding = _binding(
        lease_holder_agent_id="agent-sub",
        evaluated_head_sha=HEAD,
        evaluated_spec_id=uuid.uuid4(),
        last_assessment={"outcome": "ACCEPTABLE"},
    )
    submitted = SimpleNamespace(
        status="submitted", pr_number=4, release_reason=None, spec_id=uuid.uuid4()
    )
    ledger = SimpleNamespace(status=LedgerStatus.ADVERTISED.value, amount_ate=2500)
    bounty = SimpleNamespace(status=BountyStatus.OPEN)
    db = _db()
    active = {"lease": submitted}
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=binding)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_active_lease",
        _active_now(active),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_ledger_entry", AsyncMock(return_value=ledger)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_bounty", AsyncMock(return_value=bounty)
    )
    _run(db, _job(JobKind.ISSUE_LABELED, _issue_data([{"name": "bug"}]), action="unlabeled"))
    assert binding.state == BindingState.CANCELLED.value
    assert binding.pr_number is None
    assert binding.lease_holder_agent_id is None
    assert binding.evaluated_head_sha is None
    assert binding.last_assessment is None
    assert submitted.status == "released"
    assert submitted.release_reason == "bounty_cancelled"
    assert ledger.status == LedgerStatus.RELEASED.value
    assert bounty.status == BountyStatus.CANCELLED
    assert renders["render_cancelled"].call_args.kwargs["reason"] == "bounty label removed"

    frozen = _binding(state=BindingState.SETTLEMENT_PENDING.value, pr_number=4)
    frozen_lease = SimpleNamespace(status="submitted", pr_number=4, release_reason=None)
    active["lease"] = frozen_lease
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=frozen)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_acceptance_spec",
        AsyncMock(side_effect=AssertionError("settled binding was edited")),
    )
    _run(db, _job(JobKind.ISSUE_LABELED, _issue_data([])))
    assert frozen.state == BindingState.SETTLEMENT_PENDING.value
    assert frozen.pr_number == 4
    assert frozen_lease.status == "submitted"
    assert frozen_lease.release_reason is None


def _active_now(active):
    async def _lookup(_db, _binding_id):
        return active["lease"]

    return _lookup


def test_issue_closed_cancels_advertised_binding(monkeypatch):
    _patch_comments(monkeypatch)
    binding = _binding(
        state=BindingState.NEEDS_REVIEW.value,
        pr_number=4,
        lease_holder_agent_id="agent-work",
    )
    work = SimpleNamespace(status="work", pr_number=None, release_reason=None, spec_id=uuid.uuid4())
    ledger = SimpleNamespace(status=LedgerStatus.ADVERTISED.value)
    bounty = SimpleNamespace(status=BountyStatus.OPEN)
    db = _db()
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue", AsyncMock(return_value=binding)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_active_lease", AsyncMock(return_value=work)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_ledger_entry", AsyncMock(return_value=ledger)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_bounty", AsyncMock(return_value=bounty)
    )
    data = _issue_data([{"name": "bounty: $25"}])
    _run(db, _job(JobKind.ISSUE_CLOSED, data, event="issues", action="closed"))
    assert binding.state == BindingState.CANCELLED.value
    assert binding.pr_number == 4
    assert binding.lease_holder_agent_id == "agent-work"
    assert work.status == "released"
    assert work.release_reason == "bounty_cancelled"
    assert ledger.status == LedgerStatus.RELEASED.value
    assert bounty.status == BountyStatus.CANCELLED


def _checks_job(head=HEAD, number=4):
    return _job(
        JobKind.CHECKS_COMPLETED,
        _checks_data(head, number),
        event="check_suite",
        action="completed",
    )


def _checks_data(head=HEAD, number=4):
    return {
        "installation": {"id": 42},
        "repository": {"full_name": REPO},
        "check_suite": {"head_sha": head, "pull_requests": [{"number": number}]},
    }


def _open_pr(head=HEAD):
    return {
        "number": 4,
        "state": "open",
        "head": {"sha": head},
        "base": {"sha": BASE},
        "user": {"login": "solver"},
    }


def _prepare_checks(monkeypatch, binding, spec, runs, pr=None, lease=None, *, with_lease=True):
    _patch_comments(monkeypatch)
    if with_lease and lease is None:
        holder = getattr(binding, "lease_holder_agent_id", None) or "agent-7"
        lease = SimpleNamespace(
            id=uuid.uuid4(),
            binding_id=binding.id,
            agent_id=holder,
            user_id=uuid.uuid4(),
            spec_id=spec.id,
            status="submitted",
            pr_number=binding.pr_number,
            release_reason=None,
        )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_pull_request",
        AsyncMock(return_value=binding),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_submitted_lease",
        AsyncMock(return_value=lease if with_lease else None),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_acceptance_spec", AsyncMock(return_value=spec)
    )
    monkeypatch.setattr(github_app, "get_pull_request", AsyncMock(return_value=pr or _open_pr()))
    monkeypatch.setattr(github_app, "list_check_runs", AsyncMock(return_value=runs))
    compare = AsyncMock(return_value="diff --git a/x b/x\n")
    verify = AsyncMock(
        return_value={
            "outcome": "ACCEPTABLE",
            "findings": [],
            "reviewer_opinion": {"status": "ok", "summary": "looks fine"},
            "trace_consistency": {"ok": True},
            "diff_sha256": "deadbeef",
        }
    )
    monkeypatch.setattr(github_app, "compare_diff", compare)
    monkeypatch.setattr(mediator, "verify_patch", verify)
    return compare, verify


def test_checks_pending_does_not_call_the_mediator(monkeypatch):
    binding = _binding()
    spec = _spec(binding.id)
    binding.current_spec_id = spec.id
    _compare, verify = _prepare_checks(
        monkeypatch, binding, spec, [_run_check(status="in_progress", conclusion=None)]
    )
    _run(_db(), _checks_job())
    verify.assert_not_called()
    _compare.assert_not_called()
    assert binding.last_assessment is None
    assert binding.state == BindingState.ADVERTISED.value


def test_checks_failed_records_rejected_checks_without_mediator(monkeypatch):
    binding = _binding()
    spec = _spec(binding.id)
    binding.current_spec_id = spec.id
    _prepare_checks(monkeypatch, binding, spec, [_run_check(conclusion="failure")])
    _run(_db(), _checks_job())
    mediator.verify_patch.assert_not_called()
    github_app.compare_diff.assert_not_called()
    assert binding.state == BindingState.ADVERTISED.value
    assert binding.last_assessment == {
        "outcome": "REJECTED_CHECKS",
        "failed_checks": ["ci"],
        "head_sha": HEAD,
    }
    assert binding.evaluated_head_sha is None
    kwargs = github_comments.render_evaluation.call_args.kwargs
    assert kwargs["outcome"] == "REJECTED_CHECKS"
    assert kwargs["failed_checks"] == ["ci"]


def test_checks_passed_sends_github_diff_and_frozen_spec_to_mediator(monkeypatch):
    binding = _binding(lease_holder_agent_id="agent-7", review_cleared_by="old-reviewer")
    spec = _spec(binding.id, content_hash="frozen-hash", policy={"sealed": True})
    binding.current_spec_id = spec.id
    compare, verify = _prepare_checks(monkeypatch, binding, spec, [_run_check()])
    _run(_db(), _checks_job())
    compare.assert_awaited_once_with(42, REPO, BASE, HEAD)
    github_app.list_check_runs.assert_awaited_once_with(42, REPO, HEAD)
    assert verify.await_args.args[0] == {
        "spec_hash": "frozen-hash",
        "repo": REPO,
        "pr_number": 4,
        "head_sha": HEAD,
        "git_diff": "diff --git a/x b/x\n",
        "policy": {"sealed": True},
        "bounty_id": str(binding.bounty_id),
        "solver_agent_id": "agent-7",
        "enable_reviewer": True,
    }
    assert verify.await_args.args[0]["policy"] is not spec.policy
    assert binding.evaluated_head_sha == HEAD
    assert binding.evaluated_spec_id == spec.id
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert binding.review_cleared_by is None
    assert binding.last_assessment["outcome"] == "ACCEPTABLE"
    assert binding.last_assessment["spec_id"] == str(spec.id)
    assert binding.last_assessment["diff_sha256"] == "deadbeef"

    _run(_db(), _checks_job())
    assert verify.await_count == 1
    assert compare.await_count == 1
    # A passing rerun re-reads checks and does not call the mediator again.
    assert github_app.get_pull_request.await_count == 2
    assert github_app.list_check_runs.await_count == 2
    assert binding.state == BindingState.AWAITING_MERGE.value


def test_stored_evaluation_state_keeps_a_review_clear():
    cleared = SimpleNamespace(
        review_cleared_by="maintainer",
        last_assessment={"outcome": "NEEDS_REVIEW", "findings": [{"severity": "review"}]},
    )
    assert bounty_svc._state_for_stored_evaluation(cleared) == BindingState.AWAITING_MERGE.value
    cleared.last_assessment = {
        "outcome": "NEEDS_REVIEW",
        "findings": [{"severity": "violation", "rule": "secrets"}],
    }
    assert bounty_svc._state_for_stored_evaluation(cleared) == BindingState.NEEDS_REVIEW.value
    cleared.review_cleared_by = None
    cleared.last_assessment = {"outcome": "ACCEPTABLE", "findings": []}
    assert bounty_svc._state_for_stored_evaluation(cleared) == BindingState.AWAITING_MERGE.value
    cleared.last_assessment = {"outcome": "NEEDS_REVIEW", "findings": []}
    assert bounty_svc._state_for_stored_evaluation(cleared) == BindingState.NEEDS_REVIEW.value


@pytest.mark.parametrize(
    ("state", "outcome", "runs", "comment_kwargs"),
    [
        (
            BindingState.AWAITING_MERGE.value,
            "ACCEPTABLE",
            [_run_check(conclusion="failure")],
            {"outcome": "REJECTED_CHECKS", "failed_checks": ["ci"]},
        ),
        (
            BindingState.NEEDS_REVIEW.value,
            "NEEDS_REVIEW",
            [_run_check(status="in_progress", conclusion=None)],
            {"outcome": "NOT_ACCEPTED", "reviewer_summary": REASON_CHECKS_NOT_PASSING},
        ),
        (
            BindingState.AWAITING_MERGE.value,
            "ACCEPTABLE",
            [],
            {"outcome": "NOT_ACCEPTED", "reviewer_summary": REASON_CHECKS_NOT_PASSING},
        ),
    ],
)
def test_check_rerun_reverts_merge_gate_without_calling_mediator(
    monkeypatch, state, outcome, runs, comment_kwargs
):
    binding = _binding(
        state=state,
        evaluated_head_sha=HEAD,
        lease_holder_agent_id="agent-7",
        last_assessment={
            "outcome": outcome,
            "findings": [],
            "reviewer_opinion": {"summary": "looks fine"},
        },
    )
    spec = _spec(binding.id)
    binding.current_spec_id = spec.id
    binding.evaluated_spec_id = spec.id
    _compare, verify = _prepare_checks(monkeypatch, binding, spec, runs)
    _run(_db(), _checks_job())
    verify.assert_not_called()
    github_app.compare_diff.assert_not_called()
    assert binding.state == BindingState.ADVERTISED.value
    assert binding.evaluated_head_sha == HEAD
    assert binding.evaluated_spec_id == spec.id
    assert binding.last_assessment["outcome"] == outcome
    comment = github_comments.render_evaluation.call_args.kwargs
    for key, value in comment_kwargs.items():
        assert comment[key] == value

    github_app.list_check_runs.return_value = [_run_check()]
    _run(_db(), _checks_job())
    verify.assert_not_called()
    assert binding.state == state
    assert binding.evaluated_head_sha == HEAD
    assert binding.last_assessment["outcome"] == outcome


def test_checks_without_submitted_lease_are_not_evaluated(monkeypatch):
    binding = _binding(lease_holder_agent_id="agent-7")
    spec = _spec(binding.id)
    binding.current_spec_id = spec.id
    _compare, verify = _prepare_checks(
        monkeypatch, binding, spec, [_run_check()], with_lease=False
    )
    _run(_db(), _checks_job())
    verify.assert_not_called()
    github_app.get_pull_request.assert_not_called()
    github_app.compare_diff.assert_not_called()
    assert binding.evaluated_head_sha is None
    assert binding.evaluated_spec_id is None
    assert binding.state == BindingState.ADVERTISED.value


def test_checks_use_submitted_lease_spec_when_current_spec_differs(monkeypatch):
    binding = _binding(lease_holder_agent_id="binding-agent")
    leased = _spec(binding.id, version=4, content_hash="leased-hash", policy={"sealed": True})
    binding.current_spec_id = uuid.uuid4()
    lease = SimpleNamespace(
        spec_id=leased.id,
        agent_id="lease-agent",
        status="submitted",
        pr_number=binding.pr_number,
    )
    _compare, verify = _prepare_checks(
        monkeypatch, binding, leased, [_run_check()], lease=lease
    )
    _run(_db(), _checks_job())
    looked_up = bounty_svc.get_acceptance_spec.await_args.args[1]
    assert looked_up == leased.id
    assert looked_up != binding.current_spec_id
    payload = verify.await_args.args[0]
    assert payload["spec_hash"] == "leased-hash"
    assert payload["solver_agent_id"] == "lease-agent"
    assert binding.evaluated_spec_id == leased.id
    assert binding.evaluated_head_sha == HEAD
    assert binding.state == BindingState.AWAITING_MERGE.value


def test_mediator_413_records_rejected_too_large(monkeypatch):
    binding = _binding()
    spec = _spec(binding.id)
    binding.current_spec_id = spec.id
    _compare, verify = _prepare_checks(monkeypatch, binding, spec, [_run_check()])
    request = httpx.Request("POST", "https://mediator.test/api/v1/verify-patch")
    verify.side_effect = httpx.HTTPStatusError(
        "too large", request=request, response=httpx.Response(413, request=request)
    )
    _run(_db(), _checks_job())
    assert binding.last_assessment["outcome"] == "REJECTED_TOO_LARGE"
    assert binding.state == BindingState.ADVERTISED.value
    assert github_comments.render_evaluation.call_args.kwargs["outcome"] == "REJECTED_TOO_LARGE"


def test_checks_github_5xx_is_retryable_and_4xx_is_permanent(monkeypatch):
    binding = _binding()
    spec = _spec(binding.id)
    binding.current_spec_id = spec.id
    _prepare_checks(monkeypatch, binding, spec, [])
    github_app.list_check_runs.side_effect = github_app.GitHubAPIError("unavailable", 503)
    job = _job(JobKind.CHECKS_COMPLETED, _checks_data(), event="check_suite", action="completed")
    with pytest.raises(RetryableJobError):
        _run(_db(), job)
    github_app.list_check_runs.side_effect = github_app.GitHubAPIError("missing", 404)
    with pytest.raises(PermanentJobError):
        _run(_db(), job)
    mediator.verify_patch.assert_not_called()


def _merged_pr(head, merged=True):
    return {
        "installation": {"id": 42},
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": 4,
            "merged": merged,
            "head": {"sha": head},
            "user": {"login": "solver"},
        },
    }


def _prepare_close(monkeypatch, binding, spec=None, settlement=None, lease=None, runs=None):
    renders, _upsert = _patch_comments(monkeypatch)
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_pull_request",
        AsyncMock(return_value=binding),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_acceptance_spec", AsyncMock(return_value=spec)
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_submitted_lease",
        AsyncMock(return_value=lease),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_settlement_for_binding",
        AsyncMock(return_value=settlement),
    )
    if runs is None:
        runs = [_run_check()]
    monkeypatch.setattr(github_app, "list_check_runs", AsyncMock(return_value=runs))
    return renders


def _close_lease(spec_id, **overrides):
    lease = SimpleNamespace(
        status="submitted",
        pr_number=4,
        spec_id=spec_id,
        agent_id="lease-agent",
        user_id=uuid.uuid4(),
        release_reason=None,
    )
    for key, value in overrides.items():
        setattr(lease, key, value)
    return lease


def test_merged_at_a_different_sha_does_not_settle(monkeypatch):
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=spec_id,
        evaluated_spec_id=spec_id,
        last_assessment={"findings": []},
    )
    lease = _close_lease(spec_id)
    renders = _prepare_close(
        monkeypatch, binding, spec=_spec(binding.id, id=spec_id, version=3), lease=lease
    )
    db = _db()
    _run(
        db,
        _job(JobKind.PR_CLOSED, _merged_pr("other-sha"), event="pull_request", action="closed"),
    )
    assert not any(isinstance(row, (GitHubSettlement, GitHubJob)) for row in _added(db))
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert lease.status == "submitted"
    summary = renders["render_evaluation"].call_args.kwargs["reviewer_summary"]
    assert summary == REASON_MERGED_SHA_MISMATCH
    renders["render_not_payable"].assert_not_called()


def test_merged_at_evaluated_sha_enqueues_settlement(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=uuid.uuid4(),
        evaluated_spec_id=spec_id,
        last_assessment={"outcome": "ACCEPTABLE", "findings": [{"severity": "review"}]},
        lease_holder_agent_id="binding-agent",
    )
    lease = _close_lease(spec_id, agent_id="lease-agent")
    spec = _spec(binding.id, id=spec_id)
    renders = _prepare_close(monkeypatch, binding, spec=spec, lease=lease)
    db = _db()
    job = _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed")
    _run(db, job)
    settlements = [row for row in _added(db) if isinstance(row, GitHubSettlement)]
    jobs = [row for row in _added(db) if isinstance(row, GitHubJob)]
    assert len(settlements) == 1 and len(jobs) == 1
    settlement, advance = settlements[0], jobs[0]
    assert settlement.binding_id == binding.id
    assert settlement.spec_id == lease.spec_id
    assert settlement.spec_id != binding.current_spec_id
    assert settlement.head_sha == HEAD
    assert settlement.solver_agent_id == "lease-agent"
    assert settlement.idempotency_prefix == f"gh:{binding.id}:{lease.spec_id}"
    assert advance.kind == JobKind.ADVANCE_SETTLEMENT.value
    assert advance.payload == {"settlement_id": str(settlement.id)}
    assert binding.state == BindingState.SETTLEMENT_PENDING.value
    assert lease.status == "settled"
    reason = renders["render_accepted_payment_pending"].call_args.kwargs["reason"]
    assert reason is None
    github_app.list_check_runs.assert_awaited_once_with(42, REPO, HEAD)
    adds = db.add.call_count
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_settlement_for_binding",
        AsyncMock(return_value=settlement),
    )
    _run(db, job)
    assert db.add.call_count == adds
    assert github_app.list_check_runs.await_count == 1


def test_merged_without_lease_is_not_payable(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=uuid.uuid4(),
        evaluated_spec_id=uuid.uuid4(),
        last_assessment={"findings": []},
        lease_holder_agent_id="agent-9",
    )
    renders = _prepare_close(monkeypatch, binding, lease=None)
    db = _db()
    _run(db, _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed"))
    assert db.add.call_count == 0
    assert binding.state == BindingState.AWAITING_MERGE.value
    kwargs = renders["render_not_payable"].call_args.kwargs
    assert kwargs["bounty_id"] == binding.bounty_id
    assert kwargs["reason"] == REASON_NO_LEASE_SUBMISSION
    renders["render_evaluation"].assert_not_called()
    renders["render_accepted_payment_pending"].assert_not_called()


def test_merged_when_settlement_disabled_still_enqueues(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", False)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=spec_id,
        evaluated_spec_id=spec_id,
        last_assessment={"findings": []},
        lease_holder_agent_id="agent-9",
    )
    lease = _close_lease(spec_id, agent_id="agent-9")
    renders = _prepare_close(
        monkeypatch, binding, spec=_spec(binding.id, id=spec_id), lease=lease
    )
    db = _db()
    _run(db, _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed"))
    assert any(isinstance(row, GitHubJob) for row in _added(db))
    assert lease.status == "settled"
    reason = renders["render_accepted_payment_pending"].call_args.kwargs["reason"]
    assert reason == REASON_SETTLEMENT_DISABLED


@pytest.mark.parametrize(
    "runs",
    [
        [_run_check(conclusion="failure")],
        [_run_check(status="in_progress", conclusion=None)],
        [_run_check(head_sha="ffff111222")],
        [_run_check(app_id=99)],
        [],
    ],
)
def test_merged_without_passing_head_checks_does_not_settle(monkeypatch, runs):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=spec_id,
        evaluated_spec_id=spec_id,
        last_assessment={"outcome": "ACCEPTABLE", "findings": []},
    )
    lease = _close_lease(spec_id)
    renders = _prepare_close(
        monkeypatch, binding, spec=_spec(binding.id, id=spec_id), lease=lease, runs=runs
    )
    db = _db()
    job = _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed")
    job.attempts = settings.GITHUB_JOB_MAX_ATTEMPTS
    _run(db, job)
    assert not any(isinstance(row, (GitHubSettlement, GitHubJob)) for row in _added(db))
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert lease.status == "submitted"
    kwargs = renders["render_evaluation"].call_args.kwargs
    assert kwargs["outcome"] == "NOT_ACCEPTED"
    assert kwargs["head_sha"] == HEAD
    assert kwargs["reviewer_summary"] == REASON_CHECKS_NOT_PASSING
    github_app.list_check_runs.assert_awaited_once_with(42, REPO, HEAD)


@pytest.mark.parametrize(
    "runs",
    [
        [_run_check(status="in_progress", conclusion=None)],
        [_run_check(head_sha="ffff111222")],
        [],
    ],
)
def test_merged_with_incomplete_head_checks_retries(monkeypatch, runs):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=spec_id,
        evaluated_spec_id=spec_id,
        last_assessment={"outcome": "ACCEPTABLE", "findings": []},
    )
    lease = _close_lease(spec_id)
    renders = _prepare_close(
        monkeypatch, binding, spec=_spec(binding.id, id=spec_id), lease=lease, runs=runs
    )
    db = _db()
    job = _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed")
    job.attempts = 1
    with pytest.raises(RetryableJobError):
        _run(db, job)
    assert not any(isinstance(row, (GitHubSettlement, GitHubJob)) for row in _added(db))
    assert lease.status == "submitted"
    renders["render_evaluation"].assert_not_called()


def test_merged_with_unconfigured_checks_does_not_settle(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        evaluated_spec_id=spec_id,
        current_spec_id=spec_id,
        last_assessment={"findings": []},
    )
    lease = _close_lease(spec_id)
    renders = _prepare_close(
        monkeypatch,
        binding,
        spec=_spec(binding.id, id=spec_id, required_checks=[]),
        lease=lease,
        runs=[_run_check()],
    )
    db = _db()
    _run(db, _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed"))
    assert db.add.call_count == 0
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert lease.status == "submitted"
    assert renders["render_evaluation"].call_args.kwargs["reviewer_summary"] == (
        REASON_CHECKS_NOT_PASSING
    )


def test_merged_check_lookup_unavailable_is_retryable(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        evaluated_spec_id=spec_id,
        current_spec_id=spec_id,
        last_assessment={"findings": []},
    )
    lease = _close_lease(spec_id)
    _prepare_close(monkeypatch, binding, spec=_spec(binding.id, id=spec_id), lease=lease)
    github_app.list_check_runs.side_effect = github_app.GitHubAPIError("unavailable", 503)
    db = _db()
    job = _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed")
    with pytest.raises(RetryableJobError):
        _run(db, job)
    assert db.add.call_count == 0
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert lease.status == "submitted"


def test_merged_after_hold_expired_does_not_settle(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_SETTLEMENT_ENABLED", True)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        evaluated_spec_id=spec_id,
        current_spec_id=spec_id,
        last_assessment={"findings": []},
    )
    lease = _close_lease(
        spec_id, hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    renders = _prepare_close(monkeypatch, binding, spec=_spec(binding.id, id=spec_id), lease=lease)
    db = _db()
    _run(db, _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed"))
    assert db.add.call_count == 0
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert lease.status == "submitted"
    assert renders["render_evaluation"].call_args.kwargs["reviewer_summary"] == REASON_HOLD_EXPIRED
    github_app.list_check_runs.assert_not_called()


def test_stale_spec_merge_does_not_settle(monkeypatch):
    evaluated = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        current_spec_id=evaluated,
        evaluated_spec_id=evaluated,
        last_assessment={"findings": []},
    )
    lease = _close_lease(uuid.uuid4())
    renders = _prepare_close(monkeypatch, binding, spec=_spec(binding.id, version=2), lease=lease)
    db = _db()
    _run(db, _job(JobKind.PR_CLOSED, _merged_pr(HEAD), event="pull_request", action="closed"))
    assert db.add.call_count == 0
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert lease.status == "submitted"
    assert renders["render_evaluation"].call_args.kwargs["reviewer_summary"] == REASON_STALE_SPEC
    renders["render_not_payable"].assert_not_called()


def test_unmerged_pr_releases_lease_and_unbinds(monkeypatch):
    _patch_comments(monkeypatch)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        evaluated_spec_id=spec_id,
        current_spec_id=spec_id,
        last_assessment={"outcome": "ACCEPTABLE"},
        review_cleared_by="maintainer",
        pr_number=4,
        lease_holder_agent_id="agent-7",
    )
    lease = SimpleNamespace(
        status="submitted", pr_number=4, release_reason=None, agent_id="agent-7"
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_pull_request",
        AsyncMock(return_value=binding),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_submitted_lease",
        AsyncMock(return_value=lease),
    )
    _run(
        _db(),
        _job(
            JobKind.PR_CLOSED,
            _merged_pr(HEAD, merged=False),
            event="pull_request",
            action="closed",
        ),
    )
    assert lease.status == "released"
    assert lease.release_reason == "pr_closed_unmerged"
    assert binding.pr_number is None
    assert binding.lease_holder_agent_id is None
    assert binding.state == BindingState.ADVERTISED.value
    assert binding.evaluated_head_sha is None
    assert binding.evaluated_spec_id is None
    assert binding.last_assessment is None
    assert binding.review_cleared_by is None


def test_synchronize_clears_a_new_head_on_the_bound_pull_request(monkeypatch):
    spec_id = uuid.uuid4()
    binding = _binding(
        pr_number=4,
        state=BindingState.AWAITING_MERGE.value,
        evaluated_head_sha=HEAD,
        evaluated_spec_id=spec_id,
        current_spec_id=spec_id,
        last_assessment={"outcome": "ACCEPTABLE"},
        review_cleared_by="maintainer",
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_pull_request",
        AsyncMock(return_value=binding),
    )
    same = {
        "installation": {"id": 42},
        "repository": {"full_name": REPO},
        "pull_request": {"number": 4, "body": "Fixes #12", "head": {"sha": HEAD}},
    }
    _run(_db(), _job(JobKind.PR_UPDATED, same, event="pull_request", action="synchronize"))
    assert binding.evaluated_head_sha == HEAD
    assert binding.evaluated_spec_id == spec_id
    assert binding.last_assessment == {"outcome": "ACCEPTABLE"}
    assert binding.review_cleared_by == "maintainer"
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert binding.pr_number == 4

    moved = {
        "installation": {"id": 42},
        "repository": {"full_name": REPO},
        "pull_request": {"number": 4, "body": "Fixes #12", "head": {"sha": "new-head"}},
    }
    _run(_db(), _job(JobKind.PR_UPDATED, moved, event="pull_request", action="synchronize"))
    assert binding.evaluated_head_sha is None
    assert binding.evaluated_spec_id is None
    assert binding.last_assessment is None
    assert binding.review_cleared_by is None
    assert binding.state == BindingState.ADVERTISED.value
    assert binding.pr_number == 4

    binding.state = BindingState.SETTLEMENT_PENDING.value
    binding.evaluated_head_sha = HEAD
    binding.last_assessment = {"outcome": "ACCEPTABLE"}
    _run(_db(), _job(JobKind.PR_UPDATED, moved, event="pull_request", action="synchronize"))
    assert binding.state == BindingState.SETTLEMENT_PENDING.value
    assert binding.evaluated_head_sha == HEAD


def test_closing_reference_without_a_lease_is_not_bound_or_evaluated(monkeypatch, caplog):
    binding = _binding(pr_number=None, state=BindingState.ADVERTISED.value)
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_pull_request",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_issue",
        AsyncMock(return_value=binding),
    )
    monkeypatch.setattr(
        mediator,
        "verify_patch",
        AsyncMock(side_effect=AssertionError("unleased pull request was evaluated")),
    )
    monkeypatch.setattr(
        github_app,
        "get_pull_request",
        AsyncMock(side_effect=AssertionError("unleased pull request was fetched")),
    )
    opened = {
        "installation": {"id": 42},
        "repository": {"full_name": REPO},
        "pull_request": {"number": 8, "body": "Fixes #12", "head": {"sha": HEAD}},
    }
    with caplog.at_level("INFO", logger="app.services.github_bounty_service"):
        _run(_db(), _job(JobKind.PR_UPDATED, opened, event="pull_request", action="opened"))
    assert binding.pr_number is None
    assert binding.lease_holder_agent_id is None
    assert "not payable" in caplog.text
    _run(_db(), _checks_job())
    assert binding.evaluated_head_sha is None
    assert binding.state == BindingState.ADVERTISED.value
    mediator.verify_patch.assert_not_called()


def test_review_clear_requires_write_access_a_different_author_and_no_violation(monkeypatch):
    _patch_comments(monkeypatch)
    spec_id = uuid.uuid4()
    binding = _binding(
        state=BindingState.NEEDS_REVIEW.value,
        evaluated_head_sha=HEAD,
        evaluated_spec_id=spec_id,
        current_spec_id=spec_id,
        last_assessment={
            "outcome": "NEEDS_REVIEW",
            "findings": [{"severity": "review", "rule": "size"}],
            "reviewer_opinion": {"summary": "Needs a look."},
        },
    )
    spec = _spec(binding.id, id=spec_id, version=2)
    binding.current_spec_id = uuid.uuid4()
    lease_box = {
        "lease": SimpleNamespace(
            status="submitted", pr_number=4, spec_id=spec_id, agent_id="agent-7"
        )
    }

    async def _lease(_db, _binding_id):
        return lease_box["lease"]

    monkeypatch.setattr(
        "app.services.github_bounty_service.get_binding_by_pull_request",
        AsyncMock(return_value=binding),
    )
    monkeypatch.setattr("app.services.github_bounty_service.get_submitted_lease", _lease)
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_acceptance_spec", AsyncMock(return_value=spec)
    )
    monkeypatch.setattr(
        github_app,
        "get_collaborator_permission",
        AsyncMock(return_value="write"),
    )
    monkeypatch.setattr(github_app, "get_pull_request", AsyncMock(return_value=_open_pr()))
    data = {
        "installation": {"id": 42},
        "repository": {"full_name": REPO},
        "issue": {"number": 4, "pull_request": {"url": "https://github.test/pr/4"}},
        "comment": {"user": {"login": "maintainer"}, "body": "/clear"},
    }
    job = _job(JobKind.REVIEW_CLEAR_COMMENT, data, event="issue_comment", action="created")
    _run(_db(), job)
    assert binding.state == BindingState.AWAITING_MERGE.value
    assert binding.review_cleared_by == "maintainer"
    assert bounty_svc.get_acceptance_spec.await_args.args[1] == spec_id

    binding.state = BindingState.NEEDS_REVIEW.value
    binding.review_cleared_by = None
    github_app.get_collaborator_permission.return_value = "read"
    _run(_db(), job)
    assert binding.state == BindingState.NEEDS_REVIEW.value

    github_app.get_collaborator_permission.return_value = "admin"
    github_app.get_pull_request.return_value = _open_pr() | {"user": {"login": "Maintainer"}}
    data["comment"]["user"]["login"] = "maintainer"
    _run(_db(), _job(JobKind.REVIEW_CLEAR_COMMENT, data, event="issue_comment", action="created"))
    assert binding.state == BindingState.NEEDS_REVIEW.value

    github_app.get_pull_request.return_value = _open_pr()
    binding.last_assessment = {"findings": [{"severity": "violation", "rule": "secrets"}]}
    binding.review_cleared_by = None
    _run(_db(), job)
    assert binding.state == BindingState.NEEDS_REVIEW.value
    assert binding.review_cleared_by is None

    binding.last_assessment = {
        "outcome": "NEEDS_REVIEW",
        "findings": [{"severity": "review", "rule": "size"}],
    }
    lease_box["lease"] = None
    github_app.get_collaborator_permission.return_value = "write"
    _run(_db(), job)
    assert binding.state == BindingState.NEEDS_REVIEW.value
    assert binding.review_cleared_by is None


def test_installation_upsert_disable_and_repo_link_idempotency(monkeypatch):
    db = _db()
    created = {
        "installation": {"id": 9, "account": {"login": "octo"}},
    }
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_installation", AsyncMock(return_value=None)
    )
    _run(db, _job(JobKind.INSTALLATION, created, event="installation", action="created"))
    installs = [row for row in _added(db) if isinstance(row, GitHubInstallation)]
    assert len(installs) == 1
    assert installs[0].installation_id == 9
    assert installs[0].account_login == "octo"

    existing = installs[0]
    existing.removed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.github_bounty_service.get_installation", AsyncMock(return_value=existing)
    )
    created["installation"]["account"]["login"] = "octo-renamed"
    _run(db, _job(JobKind.INSTALLATION, created, event="installation", action="created"))
    assert existing.account_login == "octo-renamed"
    assert existing.removed_at is None
    assert len([row for row in _added(db) if isinstance(row, GitHubInstallation)]) == 1

    link = SimpleNamespace(enabled=True, repo_full_name=REPO)
    monkeypatch.setattr(
        "app.services.github_bounty_service.list_repo_links_for_installation",
        AsyncMock(return_value=[link]),
    )
    _run(db, _job(JobKind.INSTALLATION, created, event="installation", action="deleted"))
    assert existing.removed_at is not None
    assert link.enabled is False

    present = SimpleNamespace(enabled=False, repo_full_name="acme/old")
    seen = {"acme/old": present}

    async def _get_link(_db, full_name):
        return seen.get(full_name)

    monkeypatch.setattr("app.services.github_bounty_service.get_repo_link", _get_link)
    added = {
        "installation": {"id": 9},
        "repositories_added": [{"full_name": "acme/old"}, {"full_name": "acme/new"}],
    }
    before = db.add.call_count
    _run(
        db,
        _job(JobKind.INSTALLATION, added, event="installation_repositories", action="added"),
    )
    new_links = [row for row in _added(db)[before:] if isinstance(row, GitHubRepoLink)]
    assert len(new_links) == 1
    assert new_links[0].repo_full_name == "acme/new"
    assert new_links[0].enabled is False
    assert new_links[0].funder_user_id is None


def test_advance_settlement_delegates_and_rejects_bad_jobs(monkeypatch):
    seen = {}

    async def _advance(db, settlement_id):
        seen["db"] = db
        seen["id"] = settlement_id

    monkeypatch.setattr(github_settlement, "advance_settlement", _advance)
    db = _db()
    settlement_id = uuid.uuid4()
    job = GitHubJob(
        kind=JobKind.ADVANCE_SETTLEMENT.value,
        payload={"settlement_id": str(settlement_id)},
    )
    _run(db, job)
    assert seen["db"] is db
    assert seen["id"] == settlement_id

    with pytest.raises(PermanentJobError):
        _run(
            db,
            GitHubJob(kind=JobKind.ADVANCE_SETTLEMENT.value, payload={"settlement_id": "nope"}),
        )
    with pytest.raises(PermanentJobError):
        _run(db, GitHubJob(kind="not-a-kind", payload={}))
    with pytest.raises(PermanentJobError):
        _run(db, _job(JobKind.CHECKS_COMPLETED, {"installation": {"id": 1}}))
