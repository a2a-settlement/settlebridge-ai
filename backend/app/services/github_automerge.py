"""Bounded auto-merge of ACCEPTABLE pull requests on repos that opt in."""

from __future__ import annotations

import logging
import math

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import github_app
from app.services.github_alerts import dispatch_for_issue

logger = logging.getLogger(__name__)

_MARKER = "<!-- settlebridge:automerge -->"
# Branch protection, a moved head, or a non-mergeable pull request.
_BLOCKED = frozenset({405, 409, 422})


def _skip(repo: str, pr_number: int, reason: str) -> None:
    logger.info("auto-merge skipped for %s#%s: %s", repo, pr_number, reason)
    return None


def _real(value: object) -> float | None:
    """Finite number, or None. Booleans and NaN do not meet a minimum."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _label_names(issue: object) -> list[str] | None:
    if not isinstance(issue, dict):
        return None
    labels = issue.get("labels")
    if not isinstance(labels, list):
        return None
    names: list[str] = []
    for label in labels:
        if isinstance(label, str):
            names.append(label)
            continue
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
            continue
        return None
    return names


def _blocked_body(status_code: int) -> str:
    return (
        f"Auto-merge was blocked by GitHub (HTTP {status_code}). "
        "A maintainer can merge this pull request normally."
    )


async def _post_blocked(installation_id: int, repo: str, pr_number: int, status_code: int) -> None:
    try:
        await github_app.upsert_sticky_comment(
            installation_id,
            repo,
            pr_number,
            _MARKER,
            _blocked_body(status_code),
        )
    except Exception as exc:
        # The function must not raise, and GitHub's message can echo a token.
        logger.info(
            "auto-merge skipped for %s#%s: could not post the block comment (%s)",
            repo,
            pr_number,
            type(exc).__name__,
        )


def _pull_request_problem(pull_request: object, head_sha: str, evaluated: object) -> str | None:
    if not isinstance(pull_request, dict):
        return "pull request payload is missing"
    if pull_request.get("state") != "open":
        return "pull request is not open"
    if pull_request.get("draft") is not False:
        return "pull request is a draft"
    head = pull_request.get("head")
    observed = head.get("sha") if isinstance(head, dict) else None
    sha_matches = isinstance(head_sha, str) and head_sha != ""
    sha_matches = sha_matches and observed == head_sha and evaluated == head_sha
    if not sha_matches:
        return "head sha does not match the evaluated commit"
    base = pull_request.get("base")
    base_repo = base.get("repo") if isinstance(base, dict) else None
    ref = base.get("ref") if isinstance(base, dict) else None
    default_branch = base_repo.get("default_branch") if isinstance(base_repo, dict) else None
    if not isinstance(ref, str) or ref == "" or ref != default_branch:
        return "base branch is not the default branch"
    additions = pull_request.get("additions")
    deletions = pull_request.get("deletions")
    if (
        isinstance(additions, bool)
        or not isinstance(additions, int)
        or isinstance(deletions, bool)
        or not isinstance(deletions, int)
    ):
        return "diff size is not a line count"
    if additions + deletions > settings.GITHUB_AUTO_MERGE_MAX_DIFF_LINES:
        return "diff exceeds the line cap"
    return None


async def _security_problem(
    db: AsyncSession, *, installation_id: int, repo: str, issue_number: int
) -> str | None:
    try:
        dispatch = await dispatch_for_issue(db, repo, issue_number)
    except Exception as exc:
        return f"alert dispatch could not be read ({type(exc).__name__})"
    if dispatch is not None:
        raw = getattr(dispatch, "alert_kind", None)
        kind = getattr(raw, "value", raw)
        # Code scanning stays with a human. Dependabot is eligible regardless of labels.
        if kind == "code_scanning":
            return "code scanning alerts need a human merge"
        if kind == "dependabot":
            return None
        return "alert dispatch is not eligible for auto-merge"
    try:
        issue = await github_app.get_issue(installation_id, repo, issue_number)
        names = _label_names(issue)
        if names is None:
            return "issue labels could not be read"
        # Lazy: github_bounty_service imports this module from the checks handler.
        from app.services import github_bounty_service

        parsed = github_bounty_service.parse_bounty_labels(names)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        detail = f"HTTP {status}" if isinstance(status, int) else type(exc).__name__
        return f"issue labels could not be read ({detail})"
    if parsed.security:
        return "security-labelled issues need a human merge"
    return None


async def _merge(installation_id: int, repo: str, pr_number: int, head_sha: str) -> str | None:
    try:
        result = await github_app.merge_pull_request(installation_id, repo, pr_number, sha=head_sha)
    except github_app.GitHubAPIError as exc:
        status = exc.status_code
        if status in _BLOCKED:
            # Status only. The API message can contain request secrets.
            _skip(repo, pr_number, f"GitHub blocked the merge (HTTP {status})")
            await _post_blocked(installation_id, repo, pr_number, int(status))
            return None
        return _skip(repo, pr_number, f"GitHub refused the merge (HTTP {status})")
    except Exception as exc:
        return _skip(repo, pr_number, f"merge raised {type(exc).__name__}")
    if not isinstance(result, dict) or result.get("merged") is not True:
        return _skip(repo, pr_number, "GitHub did not merge the pull request")
    method = result.get("merge_method")
    if not isinstance(method, str) or method == "":
        return _skip(repo, pr_number, "GitHub did not report a merge method")
    return method


async def maybe_auto_merge(
    db: AsyncSession,
    *,
    binding,
    lease,
    spec,
    link,
    installation_id: int,
    repo: str,
    pr_number: int,
    pull_request: dict,
    head_sha: str,
    assessment: dict,
) -> str | None:
    """Merge when every auto-merge condition holds. Returns the merge method used, or None.

    Never raises for an ineligible or refused merge; settlement follows from the
    ``pull_request`` closed webhook as for a human merge. ``spec`` is the spec
    that was evaluated; the decision does not read it.
    """
    try:
        if not settings.GITHUB_AUTO_MERGE_ACCEPTABLE:
            return _skip(repo, pr_number, "global auto-merge is off")
        if link.auto_merge_acceptable is not True:
            return _skip(repo, pr_number, "repo auto-merge is off")
        if not isinstance(assessment, dict) or assessment.get("outcome") != "ACCEPTABLE":
            return _skip(repo, pr_number, "outcome is not ACCEPTABLE")
        findings = assessment.get("findings")
        if not isinstance(findings, list) or findings:
            return _skip(repo, pr_number, "assessment findings are not empty")
        if binding.review_cleared_by is not None:
            return _skip(repo, pr_number, "review was cleared by a maintainer")
        kya = _real(lease.claim_kya)
        if kya is None:
            return _skip(repo, pr_number, "claim KYA is missing")
        if kya < settings.GITHUB_AUTO_MERGE_MIN_KYA:
            return _skip(repo, pr_number, "claim KYA is below the minimum")
        reputation = _real(lease.claim_reputation)
        if reputation is None:
            return _skip(repo, pr_number, "claim reputation is missing")
        if reputation < settings.GITHUB_AUTO_MERGE_MIN_EMA:
            return _skip(repo, pr_number, "claim reputation is below the minimum")
        issue_number = binding.issue_number
        if isinstance(issue_number, bool) or not isinstance(issue_number, int):
            return _skip(repo, pr_number, "issue number is missing")
        blocked = await _security_problem(
            db, installation_id=installation_id, repo=repo, issue_number=issue_number
        )
        if blocked is not None:
            return _skip(repo, pr_number, blocked)
        problem = _pull_request_problem(pull_request, head_sha, binding.evaluated_head_sha)
        if problem is not None:
            return _skip(repo, pr_number, problem)
        return await _merge(installation_id, repo, pr_number, head_sha)
    except Exception as exc:
        return _skip(repo, pr_number, f"unexpected error ({type(exc).__name__})")
