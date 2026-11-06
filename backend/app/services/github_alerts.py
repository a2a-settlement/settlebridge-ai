"""Security alerts (Dependabot, code scanning) turned into bounty issues.

A dispatched issue's specs merge ``GitHubAlertDispatch.policy_overrides`` into the
repo's default policy, and pay only after GitHub marks the alert fixed.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github import GitHubAlertDispatch, GitHubJob

logger = logging.getLogger(__name__)

ALERT_FIXED_TRIGGER = "merged_and_alert_fixed"
# Mediator rule_severity overrides that make weakening existing tests a violation.
STRICT_TEST_RULE_SEVERITY = {
    "test_file_deleted": "violation",
    "assertion_changed": "violation",
    "weak_assertion_added": "violation",
    "test_skip_added": "violation",
}


async def handle_security_alert(db: AsyncSession, job: GitHubJob) -> None:
    """``dependabot_alert`` / ``code_scanning_alert`` with ``action == "created"``."""
    raise NotImplementedError


async def dispatch_for_issue(
    db: AsyncSession, repo_full_name: str, issue_number: int
) -> GitHubAlertDispatch | None:
    return (
        await db.execute(
            select(GitHubAlertDispatch).where(
                GitHubAlertDispatch.repo_full_name == repo_full_name,
                GitHubAlertDispatch.issue_number == issue_number,
            )
        )
    ).scalar_one_or_none()


def merge_policy(base: dict, overrides: dict) -> dict:
    """``base`` with ``overrides`` applied: list values are unioned, ``rule_severity`` merged."""
    merged = dict(base or {})
    for key, value in (overrides or {}).items():
        if isinstance(value, list):
            current = merged.get(key) if isinstance(merged.get(key), list) else []
            merged[key] = list(dict.fromkeys([*current, *value]))
        elif isinstance(value, dict):
            current = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged
