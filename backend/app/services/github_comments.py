"""Markdown templates for SettleBridge GitHub bot comments. Pure functions."""

from __future__ import annotations

import uuid

_PAYMENT_NOTE = (
    "Payment happens only after the maintainer merges the PR, "
    "required checks pass on the pull request's head commit that was merged, "
    "and no policy violations are open."
)
_NO_CHECKS = "none configured — this bounty cannot be accepted until required checks are set"
_REVIEWER_LABEL = "Reviewer opinion (advisory — does not decide payment)"
_NEEDS_REVIEW_NOTE = (
    "A maintainer with write access can comment `/settlebridge clear` "
    "to clear review findings."
)
_HEADLINES = {
    "ACCEPTABLE": "Acceptable",
    "POLICY_VIOLATION": "Policy violation",
    "NEEDS_REVIEW": "Needs review",
    "REJECTED_TOO_LARGE": "Rejected — patch is too large",
    "REJECTED_CHECKS": "Rejected — required checks failed",
    "UNCONFIGURED": (
        "Not evaluated — the acceptance spec has no required checks, "
        "so this pull request cannot be accepted"
    ),
}
_FINDING_CAP = 50
_EVIDENCE_LIMIT = 200


def bounty_marker(bounty_id: uuid.UUID | str) -> str:
    return f"<!-- settlebridge:bounty:{bounty_id} -->"


def _fmt_ema(value: float) -> str:
    text = format(float(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _cell(value: object, *, limit: int | None = None) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    if limit is not None and len(text) > limit:
        text = text[:limit]
    return text.replace("|", "\\|").replace("`", "\\`")


def _location(finding: dict) -> str:
    path = finding.get("path")
    if path is None:
        path = ""
    line = finding.get("line")
    if line is None:
        return str(path)
    return f"{path}:{line}"


def _findings_lines(findings: list[dict]) -> list[str]:
    if not findings:
        return ["Findings: none"]
    shown = findings[:_FINDING_CAP]
    lines = [
        "Findings:",
        "",
        "| Rule | Severity | Path:line | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for finding in shown:
        rule = _cell(finding.get("rule", ""))
        severity = _cell(finding.get("severity", ""))
        location = _cell(_location(finding))
        evidence = _cell(finding.get("evidence", ""), limit=_EVIDENCE_LIMIT)
        lines.append(f"| {rule} | {severity} | {location} | {evidence} |")
    extra = len(findings) - len(shown)
    if extra:
        lines.append(f"+{extra} more")
    return lines


def render_advertised(
    *,
    bounty_id: uuid.UUID | str,
    amount_ate: int,
    amount_usd: str,
    spec_version: int,
    spec_hash: str,
    required_kya: int,
    required_ema: float,
    required_checks: list[dict],
    test_command: str | None,
    market_url: str,
) -> str:
    link = f"{market_url.rstrip('/')}/bounties/{bounty_id}"
    lines = [
        bounty_marker(bounty_id),
        "",
        "### SettleBridge bounty — advertised",
        "",
        f"Amount: {amount_ate} ATE (~${amount_usd})",
        "Status: balance checked; funds not reserved",
        f"Spec version: {spec_version}",
        f"Spec hash: {spec_hash[:12]}",
        f"Required KYA: {required_kya}",
        f"Minimum EMA reputation: {_fmt_ema(required_ema)}",
        "Required checks:",
    ]
    if required_checks:
        for check in required_checks:
            name = check.get("name", "")
            app_id = check.get("app_id", "")
            lines.append(f"- {name} (app {app_id})")
    else:
        lines.append(_NO_CHECKS)
    if test_command:
        lines.extend(["", "Test command:", f"```\n{test_command}\n```"])
    lines.extend(["", link, "", _PAYMENT_NOTE])
    return "\n".join(lines)


def render_not_advertised(*, reason: str) -> str:
    return f"SettleBridge did not advertise this bounty: {reason}"


def render_evaluation(
    *,
    bounty_id: uuid.UUID | str,
    outcome: str,
    head_sha: str,
    spec_version: int,
    findings: list[dict],
    failed_checks: list[str] | None = None,
    reviewer_summary: str | None = None,
) -> str:
    headline = _HEADLINES.get(outcome, outcome)
    lines = [
        bounty_marker(bounty_id),
        "",
        f"### SettleBridge evaluation — {headline}",
        "",
        f"Outcome: {outcome}",
        f"Commit: {head_sha[:7]}",
        f"Spec version: {spec_version}",
    ]
    if failed_checks:
        lines.extend(["", "Failed checks:"])
        lines.extend(f"- {name}" for name in failed_checks)
    lines.append("")
    lines.extend(_findings_lines(findings))
    if reviewer_summary is not None:
        lines.extend(["", _REVIEWER_LABEL, reviewer_summary])
    if outcome == "NEEDS_REVIEW":
        lines.extend(["", _NEEDS_REVIEW_NOTE])
    return "\n".join(lines)


def render_not_payable(*, bounty_id: uuid.UUID | str, reason: str) -> str:
    return "\n".join(
        [
            bounty_marker(bounty_id),
            "",
            "### Not payable",
            "",
            reason,
        ]
    )


def render_accepted_payment_pending(
    *, bounty_id: uuid.UUID | str, head_sha: str, reason: str | None = None
) -> str:
    lines = [
        bounty_marker(bounty_id),
        "",
        "### Patch accepted; payment pending",
        "",
        f"Commit: {head_sha[:7]}",
    ]
    if reason:
        lines.extend(["", f"Reason: {reason}"])
    return "\n".join(lines)


def render_paid(
    *, bounty_id: uuid.UUID | str, escrow_id: str, amount_ate: int, fee_ate: int | None
) -> str:
    lines = [
        bounty_marker(bounty_id),
        "",
        "### Payment completed",
        "",
        f"Escrow: {escrow_id}",
        f"Amount: {amount_ate} ATE",
    ]
    if fee_ate is not None:
        lines.append(f"Exchange fee: {fee_ate} ATE (paid by the funder)")
    return "\n".join(lines)


def render_cancelled(*, bounty_id: uuid.UUID | str, reason: str) -> str:
    return "\n".join(
        [
            bounty_marker(bounty_id),
            "",
            "### SettleBridge bounty cancelled",
            "",
            f"Reason: {reason}",
        ]
    )
