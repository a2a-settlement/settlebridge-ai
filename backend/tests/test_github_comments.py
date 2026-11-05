"""Bot comment templates. Wording is part of the pilot contract."""

from __future__ import annotations

import sys
import types
import uuid

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.services.github_comments import (  # noqa: E402
    bounty_marker,
    render_accepted_payment_pending,
    render_advertised,
    render_cancelled,
    render_evaluation,
    render_not_advertised,
    render_paid,
)

BOUNTY = uuid.UUID("12345678-1234-5678-1234-567812345678")
SHA = "abcdef1234567890"
SPEC_HASH = "0123456789abcdef" * 4


def _advertised(**overrides: object) -> str:
    kwargs = {
        "bounty_id": BOUNTY,
        "amount_ate": 5000,
        "amount_usd": "50.00",
        "spec_version": 3,
        "spec_hash": SPEC_HASH,
        "required_kya": 2,
        "required_ema": 0.75,
        "required_checks": [{"name": "ci", "app_id": 15368}, {"name": "lint", "app_id": 42}],
        "test_command": "pytest -q",
        "market_url": "https://market.example/",
    }
    kwargs.update(overrides)
    return render_advertised(**kwargs)  # type: ignore[arg-type]


def test_bounty_marker():
    assert bounty_marker(BOUNTY) == f"<!-- settlebridge:bounty:{BOUNTY} -->"
    assert bounty_marker(str(BOUNTY)) == bounty_marker(BOUNTY)


def test_advertised_checks_balance_and_does_not_claim_funds_reserved():
    body = _advertised()
    assert body.splitlines()[0] == bounty_marker(BOUNTY)
    assert "### SettleBridge bounty — advertised" in body
    assert "5000 ATE (~$50.00)" in body
    assert "balance checked; funds not reserved" in body
    assert "Spec version: 3" in body
    assert SPEC_HASH[:12] in body
    assert SPEC_HASH[:13] not in body
    assert "Required KYA: 2" in body
    assert "Minimum EMA reputation: 0.75" in body
    assert "ci (app 15368)" in body
    assert "lint (app 42)" in body
    assert "```\npytest -q\n```" in body
    assert f"https://market.example/bounties/{BOUNTY}" in body
    note = (
        "Payment happens only after the maintainer merges the PR, "
        "required checks pass on the merged commit, and no policy violations are open."
    )
    assert note in body
    lowered = body.lower()
    for banned in ("funded", "locked", "escrowed", "cheat", "tamper", "slash"):
        assert banned not in lowered
    assert "none configured" not in body


def test_advertised_without_required_checks_or_test_command():
    body = _advertised(required_checks=[], test_command=None)
    phrase = "none configured — this bounty cannot be accepted until required checks are set"
    assert phrase in body
    assert "```" not in body
    assert "balance checked; funds not reserved" in body
    assert "funded" not in body.lower()
    assert "locked" not in body.lower()


def test_not_advertised_has_no_marker():
    text = render_not_advertised(reason="repo not linked")
    assert "repo not linked" in text
    assert "<!--" not in text
    other = render_not_advertised(reason="insufficient balance")
    assert "insufficient balance" in other
    assert bounty_marker(BOUNTY) not in other


def test_evaluation_escapes_and_truncates_evidence():
    raw = "A|B`C" + ("Q" * 250)
    body = render_evaluation(
        bounty_id=BOUNTY,
        outcome="POLICY_VIOLATION",
        head_sha=SHA,
        spec_version=4,
        findings=[
            {
                "rule": "size",
                "severity": "high",
                "path": "src/a.py",
                "line": 12,
                "evidence": raw,
            }
        ],
    )
    visible = "A\\|B\\`C" + ("Q" * (200 - len("A|B`C")))
    assert visible in body
    assert "Q" * (200 - len("A|B`C") + 1) not in body
    assert "src/a.py:12" in body
    assert "Policy violation" in body
    assert SHA[:7] in body
    assert SHA[:8] not in body
    assert "Spec version: 4" in body

    newline = render_evaluation(
        bounty_id=BOUNTY,
        outcome="POLICY_VIOLATION",
        head_sha=SHA,
        spec_version=4,
        findings=[
            {"rule": "r", "severity": "low", "path": "a.py", "line": 3, "evidence": "one\ntwo"}
        ],
    )
    assert "one two" in newline
    assert "one\ntwo" not in newline


def test_evaluation_caps_findings_at_fifty_rows():
    findings = [
        {
            "rule": f"rule-{index:02d}",
            "severity": "info",
            "path": "src/a.py",
            "line": index,
            "evidence": "ok",
        }
        for index in range(51)
    ]
    body = render_evaluation(
        bounty_id=BOUNTY,
        outcome="NEEDS_REVIEW",
        head_sha=SHA,
        spec_version=1,
        findings=findings,
        failed_checks=["build"],
        reviewer_summary="advisory only",
    )
    assert body.splitlines()[0] == bounty_marker(BOUNTY)
    assert "rule-00" in body
    assert "rule-49" in body
    assert "rule-50" not in body
    assert "+1 more" in body
    assert "- build" in body
    assert "Reviewer opinion (advisory — does not decide payment)" in body
    assert "advisory only" in body
    note = (
        "A maintainer with write access can comment `/settlebridge clear` "
        "to clear review findings."
    )
    assert note in body
    assert "Needs review" in body

    exact = render_evaluation(
        bounty_id=BOUNTY,
        outcome="ACCEPTABLE",
        head_sha=SHA,
        spec_version=1,
        findings=findings[:50],
    )
    assert "rule-49" in exact
    assert "+1 more" not in exact
    assert "/settlebridge clear" not in exact
    assert "Findings: none" not in exact


def test_evaluation_without_findings():
    body = render_evaluation(
        bounty_id=BOUNTY,
        outcome="REJECTED_CHECKS",
        head_sha=SHA,
        spec_version=2,
        findings=[],
        failed_checks=["lint"],
    )
    assert "Rejected — required checks failed" in body
    assert "Findings: none" in body
    assert "- lint" in body
    assert "cheat" not in body.lower()
    assert "tamper" not in body.lower()
    assert "slash" not in body.lower()


def test_payment_pending_never_says_paid_or_completed():
    pending = render_accepted_payment_pending(
        bounty_id=BOUNTY, head_sha=SHA, reason="solver not attributed"
    )
    assert pending.splitlines()[0] == bounty_marker(BOUNTY)
    assert "Patch accepted; payment pending" in pending
    assert "solver not attributed" in pending
    assert "settlement disabled" not in pending
    disabled = render_accepted_payment_pending(
        bounty_id=BOUNTY, head_sha=SHA, reason="settlement disabled"
    )
    assert "settlement disabled" in disabled
    bare = render_accepted_payment_pending(bounty_id=BOUNTY, head_sha=SHA)
    for body in (pending, disabled, bare):
        lowered = body.lower()
        assert "paid" not in lowered
        assert "completed" not in lowered
    assert "Reason:" not in bare


def test_paid_and_cancelled():
    paid = render_paid(bounty_id=BOUNTY, escrow_id="escrow-9", amount_ate=5000, fee_ate=500)
    assert paid.splitlines()[0] == bounty_marker(BOUNTY)
    assert "Payment completed" in paid
    assert "escrow-9" in paid
    assert "5000 ATE" in paid
    assert "Clearing fee: 500 ATE" in paid
    cancelled = render_cancelled(bounty_id=BOUNTY, reason="issue closed")
    assert cancelled.splitlines()[0] == bounty_marker(BOUNTY)
    assert "cancelled" in cancelled.lower()
    assert "issue closed" in cancelled


def test_templates_are_deterministic():
    advertised = _advertised()
    assert advertised == _advertised()
    finding = {"rule": "size", "severity": "high", "path": "a.py", "line": 1, "evidence": "big"}
    evaluation = render_evaluation(
        bounty_id=BOUNTY,
        outcome="REJECTED_TOO_LARGE",
        head_sha=SHA,
        spec_version=8,
        findings=[finding],
        reviewer_summary="too big",
    )
    assert "Rejected — patch is too large" in evaluation
    assert evaluation == render_evaluation(
        bounty_id=BOUNTY,
        outcome="REJECTED_TOO_LARGE",
        head_sha=SHA,
        spec_version=8,
        findings=[finding],
        reviewer_summary="too big",
    )
    pending = render_accepted_payment_pending(
        bounty_id=BOUNTY, head_sha=SHA, reason="solver not attributed"
    )
    assert pending == render_accepted_payment_pending(
        bounty_id=BOUNTY, head_sha=SHA, reason="solver not attributed"
    )
