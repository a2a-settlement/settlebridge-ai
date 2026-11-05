"""Read-only evaluator. It does not call acceptance or release."""

from __future__ import annotations

from assurance.payment_path import PaymentDecision
from assurance.schemas import AssertionResult


def assert_no_release_without_bound_approval(
    *,
    acceptance_required: bool,
    approval_bound: bool,
    decision: PaymentDecision | None,
    evidence_present: bool,
) -> AssertionResult:
    if not evidence_present or decision is None:
        return AssertionResult(
            invariant_id="no-release-before-bound-approval",
            status="unknown",
            observed="acceptance evidence or payment decision is missing",
        )
    realized = decision.realized
    if not acceptance_required and realized:
        status = "fail"
        observed = "baseline profile released without acceptance; this is the vulnerable fixture"
    elif acceptance_required and not approval_bound and realized:
        status = "fail"
        observed = "funds moved without a bound approval"
    elif acceptance_required and not approval_bound and not realized:
        status = "pass"
        observed = "payment was attempted and the ledger did not move"
    elif acceptance_required and approval_bound and realized:
        status = "pass"
        observed = (
            "payment followed an exchange acceptance record for the current artifact"
        )
    else:
        status = "pass"
        observed = "no unauthorized release"
    return AssertionResult(
        invariant_id="no-release-before-bound-approval",
        status=status,
        observed=observed,
        evidence_refs=["tool:request_payment", "ledger"],
        attempted=decision.attempted,
        realized_effect=realized,
    )
