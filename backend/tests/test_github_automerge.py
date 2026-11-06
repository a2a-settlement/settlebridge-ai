"""Bounded auto-merge eligibility. Fakes only: no database and no network."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import sys
import types

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest

from app.config import settings
from app.services import github_automerge
from app.services.github_app import GitHubAPIError
from app.services.github_automerge import maybe_auto_merge

HEAD = "a" * 40
OTHER = "b" * 40
REPO = "acme/widget"
PR = 8
ISSUE = 41
INSTALLATION = 15
_SECRET = "ghp_SUPERSECRET"


def _binding(**overrides) -> types.SimpleNamespace:
    values = {
        "issue_number": ISSUE,
        "review_cleared_by": None,
        "evaluated_head_sha": HEAD,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


def _lease(**overrides) -> types.SimpleNamespace:
    values = {"claim_kya": 2, "claim_reputation": 0.90}
    values.update(overrides)
    return types.SimpleNamespace(**values)


def _pull(**overrides) -> dict:
    omitted = overrides.pop("omit", ())
    pull = {
        "state": "open",
        "draft": False,
        "head": {"sha": HEAD},
        "base": {"ref": "main", "repo": {"default_branch": "main"}},
        "additions": 10,
        "deletions": 4,
    }
    pull.update(overrides)
    for key in omitted:
        pull.pop(key, None)
    return pull


def _args(**overrides) -> dict:
    values = {
        "binding": _binding(),
        "lease": _lease(),
        "spec": types.SimpleNamespace(content_hash="spec"),
        "link": types.SimpleNamespace(auto_merge_acceptable=True),
        "installation_id": INSTALLATION,
        "repo": REPO,
        "pr_number": PR,
        "pull_request": _pull(),
        "head_sha": HEAD,
        "assessment": {"outcome": "ACCEPTABLE", "findings": []},
    }
    values.update(overrides)
    return values


class _Gate:
    def __init__(self) -> None:
        self.dispatch = None
        self.issue = {"labels": [{"name": "bounty: $10"}]}
        self.issue_error: BaseException | None = GitHubAPIError(
            "labels were not supposed to be read", status_code=500
        )
        self.merge_error: BaseException | None = None
        self.merge_result = {"merged": True, "sha": HEAD, "merge_method": "squash"}
        self.comment_error: BaseException | None = None
        self.merge_calls: list[dict] = []
        self.issue_calls: list[tuple] = []
        self.comment_calls: list[dict] = []
        self.dispatch_calls: list[tuple] = []

    def allow_labels(self, labels: list | None = None) -> None:
        self.issue_error = None
        if labels is not None:
            self.issue = {"labels": labels}

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "GITHUB_AUTO_MERGE_ACCEPTABLE", True)
        monkeypatch.setattr(settings, "GITHUB_AUTO_MERGE_MIN_KYA", 2)
        monkeypatch.setattr(settings, "GITHUB_AUTO_MERGE_MIN_EMA", 0.90)
        monkeypatch.setattr(settings, "GITHUB_AUTO_MERGE_MAX_DIFF_LINES", 100)
        gate = self

        async def dispatch(db, repo, issue_number):
            gate.dispatch_calls.append((repo, issue_number))
            return gate.dispatch

        async def get_issue(installation_id, repo, issue_number):
            gate.issue_calls.append((installation_id, repo, issue_number))
            if gate.issue_error is not None:
                raise gate.issue_error
            return gate.issue

        async def merge(installation_id, repo, pr_number, sha, merge_method="merge"):
            gate.merge_calls.append(
                {
                    "installation_id": installation_id,
                    "repo": repo,
                    "pr_number": pr_number,
                    "sha": sha,
                    "merge_method": merge_method,
                }
            )
            if gate.merge_error is not None:
                raise gate.merge_error
            return gate.merge_result

        async def comment(installation_id, repo, issue_number, marker, body, comment_id=None):
            gate.comment_calls.append(
                {
                    "installation_id": installation_id,
                    "repo": repo,
                    "issue_number": issue_number,
                    "marker": marker,
                    "body": body,
                    "comment_id": comment_id,
                }
            )
            if gate.comment_error is not None:
                raise gate.comment_error
            return 1

        monkeypatch.setattr(github_automerge, "dispatch_for_issue", dispatch)
        monkeypatch.setattr(github_automerge.github_app, "get_issue", get_issue)
        monkeypatch.setattr(github_automerge.github_app, "merge_pull_request", merge)
        monkeypatch.setattr(github_automerge.github_app, "upsert_sticky_comment", comment)


@pytest.fixture()
def gate(monkeypatch):
    harness = _Gate()
    harness.install(monkeypatch)
    return harness


def _invoke(caplog, **overrides):
    with caplog.at_level("INFO", logger="app.services.github_automerge"):
        return asyncio.run(maybe_auto_merge(object(), **_args(**overrides)))


def _skipped(caplog, reason: str) -> None:
    assert reason in caplog.text
    assert caplog.text.count("auto-merge skipped") == 1


def test_merges_when_every_condition_holds(gate, caplog):
    gate.allow_labels([{"name": "bounty: $10"}, {"name": "tier: kya-2"}])
    result = _invoke(caplog)
    assert result == "squash"
    assert gate.merge_calls == [
        {
            "installation_id": INSTALLATION,
            "repo": REPO,
            "pr_number": PR,
            "sha": HEAD,
            "merge_method": "merge",
        }
    ]
    assert gate.dispatch_calls == [(REPO, ISSUE)]
    assert gate.issue_calls == [(INSTALLATION, REPO, ISSUE)]
    assert gate.comment_calls == []
    assert "auto-merge skipped" not in caplog.text


def test_merges_when_the_base_matches_a_non_main_default_branch(gate, caplog):
    gate.allow_labels()
    result = _invoke(
        caplog,
        pull_request=_pull(base={"ref": "trunk", "repo": {"default_branch": "trunk"}}),
    )
    assert result == "squash"
    assert gate.merge_calls[0]["sha"] == HEAD


def test_logs_only_the_first_failing_reason(gate, caplog, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_AUTO_MERGE_ACCEPTABLE", False)
    result = _invoke(
        caplog,
        link=types.SimpleNamespace(auto_merge_acceptable=False),
        assessment={"outcome": "NEEDS_REVIEW", "findings": [{"severity": "review"}]},
    )
    assert result is None
    assert gate.merge_calls == []
    assert gate.dispatch_calls == []
    _skipped(caplog, "global auto-merge is off")
    assert "repo auto-merge is off" not in caplog.text


@pytest.mark.parametrize(
    ("overrides", "reason", "with_labels"),
    [
        pytest.param(
            {"link": types.SimpleNamespace(auto_merge_acceptable=False)},
            "repo auto-merge is off",
            False,
            id="repo-opt-out",
        ),
        pytest.param(
            {"assessment": {"outcome": "NEEDS_REVIEW", "findings": []}},
            "outcome is not ACCEPTABLE",
            False,
            id="outcome",
        ),
        pytest.param(
            {"assessment": {"outcome": "ACCEPTABLE", "findings": [{"severity": "review"}]}},
            "assessment findings are not empty",
            False,
            id="review-finding",
        ),
        pytest.param(
            {
                "assessment": {
                    "outcome": "ACCEPTABLE",
                    "findings": [{"severity": "violation", "rule": "test_file_deleted"}],
                }
            },
            "assessment findings are not empty",
            False,
            id="violation-finding",
        ),
        pytest.param(
            {"assessment": {"outcome": "ACCEPTABLE"}},
            "assessment findings are not empty",
            False,
            id="findings-missing",
        ),
        pytest.param(
            {"binding": _binding(review_cleared_by="octocat")},
            "review was cleared by a maintainer",
            False,
            id="review-cleared",
        ),
        pytest.param(
            {"lease": _lease(claim_kya=None)},
            "claim KYA is missing",
            False,
            id="kya-missing",
        ),
        pytest.param(
            {"lease": _lease(claim_kya=1)},
            "claim KYA is below the minimum",
            False,
            id="kya-low",
        ),
        pytest.param(
            {"lease": _lease(claim_kya=float("nan"))},
            "claim KYA is missing",
            False,
            id="kya-nan",
        ),
        pytest.param(
            {"lease": _lease(claim_reputation=None)},
            "claim reputation is missing",
            False,
            id="reputation-missing",
        ),
        pytest.param(
            {"lease": _lease(claim_reputation=0.89)},
            "claim reputation is below the minimum",
            False,
            id="reputation-low",
        ),
        pytest.param(
            {"pull_request": _pull(state="closed")},
            "pull request is not open",
            True,
            id="closed",
        ),
        pytest.param(
            {"pull_request": _pull(draft=True)},
            "pull request is a draft",
            True,
            id="draft",
        ),
        pytest.param(
            {"pull_request": _pull(head={"sha": OTHER})},
            "head sha does not match the evaluated commit",
            True,
            id="head-sha",
        ),
        pytest.param(
            {"binding": _binding(evaluated_head_sha=OTHER)},
            "head sha does not match the evaluated commit",
            True,
            id="evaluated-sha",
        ),
        pytest.param(
            {"head_sha": OTHER},
            "head sha does not match the evaluated commit",
            True,
            id="argument-sha",
        ),
        pytest.param(
            {"pull_request": _pull(base={"ref": "develop", "repo": {"default_branch": "main"}})},
            "base branch is not the default branch",
            True,
            id="base-branch",
        ),
        pytest.param(
            {"pull_request": _pull(additions=True, deletions=0)},
            "diff size is not a line count",
            True,
            id="diff-not-int",
        ),
        pytest.param(
            {"pull_request": _pull(omit=("additions",))},
            "diff size is not a line count",
            True,
            id="diff-missing",
        ),
    ],
)
def test_one_failing_condition_skips_the_merge(gate, caplog, overrides, reason, with_labels):
    if with_labels:
        gate.allow_labels()
    result = _invoke(caplog, **overrides)
    assert result is None
    assert gate.merge_calls == []
    _skipped(caplog, reason)


def test_dependabot_dispatch_merges_despite_a_security_label(gate, caplog):
    gate.dispatch = types.SimpleNamespace(alert_kind="dependabot")
    gate.allow_labels([{"name": "tier: security"}, {"name": "bounty: $15"}])
    result = _invoke(caplog)
    assert result == "squash"
    assert gate.merge_calls[0]["sha"] == HEAD
    assert "auto-merge skipped" not in caplog.text


def test_code_scanning_dispatch_is_not_eligible(gate, caplog):
    gate.dispatch = types.SimpleNamespace(alert_kind="code_scanning")
    gate.allow_labels([{"name": "bounty: $25"}])
    result = _invoke(caplog)
    assert result is None
    assert gate.merge_calls == []
    _skipped(caplog, "code scanning alerts need a human merge")


def test_security_label_without_a_dispatch_is_not_eligible(gate, caplog):
    gate.allow_labels([{"name": "tier: security"}, {"name": "bounty: $10"}])
    result = _invoke(caplog)
    assert result is None
    assert gate.merge_calls == []
    assert gate.issue_calls == [(INSTALLATION, REPO, ISSUE)]
    _skipped(caplog, "security-labelled issues need a human merge")


@pytest.mark.parametrize(
    "error",
    [
        GitHubAPIError(_SECRET, status_code=502),
        RuntimeError(_SECRET),
    ],
)
def test_label_lookup_errors_are_not_eligible(gate, caplog, error):
    gate.issue_error = error
    result = _invoke(caplog)
    assert result is None
    assert gate.merge_calls == []
    assert gate.comment_calls == []
    _skipped(caplog, "issue labels could not be read")
    assert _SECRET not in caplog.text


@pytest.mark.parametrize(
    ("additions", "deletions", "expect_merge"),
    [
        (100, 0, True),
        (0, 100, True),
        (50, 50, True),
        (101, 0, False),
        (0, 101, False),
        (50, 51, False),
    ],
)
def test_diff_cap_boundary(gate, caplog, additions, deletions, expect_merge):
    gate.allow_labels()
    result = _invoke(caplog, pull_request=_pull(additions=additions, deletions=deletions))
    if expect_merge:
        assert result == "squash"
        assert gate.merge_calls[0]["sha"] == HEAD
        assert "auto-merge skipped" not in caplog.text
    else:
        assert result is None
        assert gate.merge_calls == []
        _skipped(caplog, "diff exceeds the line cap")


@pytest.mark.parametrize("status", [405, 409, 422])
def test_github_block_posts_a_sticky_comment_and_returns_none(gate, caplog, status):
    gate.allow_labels()
    gate.merge_error = GitHubAPIError(_SECRET, status_code=status)
    result = _invoke(caplog)
    assert result is None
    assert gate.merge_calls[0]["sha"] == HEAD
    assert len(gate.comment_calls) == 1
    comment = gate.comment_calls[0]
    assert comment["issue_number"] == PR
    assert comment["issue_number"] != ISSUE
    assert comment["marker"] == "<!-- settlebridge:automerge -->"
    assert f"HTTP {status}" in comment["body"]
    assert "maintainer" in comment["body"].lower()
    assert _SECRET not in comment["body"]
    assert _SECRET not in caplog.text
    assert f"GitHub blocked the merge (HTTP {status})" in caplog.text


def test_other_github_status_does_not_comment(gate, caplog):
    gate.allow_labels()
    gate.merge_error = GitHubAPIError(_SECRET, status_code=500)
    result = _invoke(caplog)
    assert result is None
    assert gate.comment_calls == []
    assert _SECRET not in caplog.text
    _skipped(caplog, "GitHub refused the merge (HTTP 500)")


def test_unexpected_merge_exception_returns_none(gate, caplog):
    gate.allow_labels()
    gate.merge_error = RuntimeError(_SECRET)
    result = _invoke(caplog)
    assert result is None
    assert gate.comment_calls == []
    assert _SECRET not in caplog.text
    _skipped(caplog, "merge raised RuntimeError")


def test_sticky_comment_failure_does_not_raise(gate, caplog):
    gate.allow_labels()
    gate.merge_error = GitHubAPIError("blocked", status_code=409)
    gate.comment_error = RuntimeError("down")
    result = _invoke(caplog)
    assert result is None
    assert "GitHub blocked the merge (HTTP 409)" in caplog.text
    assert "could not post the block comment (RuntimeError)" in caplog.text


def test_unmerged_result_returns_none(gate, caplog):
    gate.allow_labels()
    gate.merge_result = {"merged": False, "sha": HEAD, "merge_method": "merge"}
    result = _invoke(caplog)
    assert result is None
    assert gate.comment_calls == []
    _skipped(caplog, "GitHub did not merge the pull request")
