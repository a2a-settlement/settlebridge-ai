"""Operator edits for GitHub repo links the App installation webhooks created.

These helpers never insert a link and never commit. The caller commits or rolls
back. A repository is bounty-eligible only when the link is enabled and has a
funder; enabling also requires a required check and an installation that is
still present.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github import GitHubInstallation, GitHubRepoLink
from app.models.user import User
from app.services.github_alerts import STRICT_TEST_RULE_SEVERITY


class RepoAdminError(Exception):
    """The repo link cannot be changed as requested."""


_KNOWN_VISIBILITY = frozenset({"public", "private", "internal"})


def _repo_name(repo: object) -> str:
    if not isinstance(repo, str) or repo.strip() == "":
        raise RepoAdminError("repo is required")
    return repo.strip()


def _policy_dict(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _require_bool(value: object, label: str) -> None:
    if value is not None and not isinstance(value, bool):
        raise RepoAdminError(f"{label} must be true or false")


def _strict_tests_enabled(policy: dict) -> bool:
    severity = policy.get("rule_severity")
    if not isinstance(severity, dict):
        return False
    return all(severity.get(rule) == level for rule, level in STRICT_TEST_RULE_SEVERITY.items())


def _apply_strict_tests(policy: dict, enabled: bool) -> None:
    severity = policy.get("rule_severity")
    current = dict(severity) if isinstance(severity, dict) else {}
    if enabled:
        current.update(STRICT_TEST_RULE_SEVERITY)
        policy["rule_severity"] = current
        return
    if not isinstance(severity, dict):
        return
    for rule in STRICT_TEST_RULE_SEVERITY:
        current.pop(rule, None)
    if current:
        policy["rule_severity"] = current
    else:
        # Those four rules were the whole map, so the empty key goes away.
        policy.pop("rule_severity", None)


def _plain_list(value: object) -> list:
    if isinstance(value, list):
        return list(value)
    return []


def _checks_copy(value: object) -> list:
    if not isinstance(value, list):
        return []
    copied = []
    for item in value:
        copied.append(dict(item) if isinstance(item, dict) else item)
    return copied


def _validated_checks(checks: object) -> list[dict]:
    if not isinstance(checks, list):
        raise RepoAdminError("checks must be a list of {name, app_id}")
    seen: set[tuple[str, int]] = set()
    cleaned: list[dict] = []
    for index, item in enumerate(checks):
        if not isinstance(item, dict):
            raise RepoAdminError(f"check {index} must be an object with name and app_id")
        name = item.get("name")
        app_id = item.get("app_id")
        if not isinstance(name, str) or name.strip() == "":
            raise RepoAdminError(f"check {index} name must be a non-empty string")
        if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
            raise RepoAdminError(f"check {index} app_id must be a positive integer")
        key = (name.strip(), app_id)
        if key in seen:
            raise RepoAdminError(f"duplicate check {key[0]}:{key[1]}")
        seen.add(key)
        cleaned.append({"name": key[0], "app_id": app_id})
    return cleaned


def _checks_are_configured(checks: object) -> bool:
    if not isinstance(checks, list) or not checks:
        return False
    try:
        return len(_validated_checks(checks)) > 0
    except RepoAdminError:
        return False


def _validated_strings(values: object, label: str) -> list[str]:
    if not isinstance(values, list):
        raise RepoAdminError(f"{label} must be a list of strings")
    cleaned = []
    for index, item in enumerate(values):
        if not isinstance(item, str) or item.strip() == "":
            raise RepoAdminError(f"{label} {index} must be a non-empty string")
        cleaned.append(item.strip())
    return cleaned


async def _require_link(db: AsyncSession, repo: str) -> GitHubRepoLink:
    name = _repo_name(repo)
    stmt = select(GitHubRepoLink).where(
        func.lower(GitHubRepoLink.repo_full_name) == name.casefold()
    )
    try:
        link = (await db.execute(stmt)).scalar_one_or_none()
    except MultipleResultsFound as exc:
        raise RepoAdminError(f"more than one repo link matches {name}") from exc
    if link is None:
        raise RepoAdminError(f"install the GitHub App on this repo first: {name}")
    return link


async def _require_user(db: AsyncSession, email: str) -> User:
    if not isinstance(email, str) or email.strip() == "":
        raise RepoAdminError("funder email is required")
    wanted = email.strip().casefold()
    stmt = select(User).where(func.lower(User.email) == wanted)
    try:
        user = (await db.execute(stmt)).scalar_one_or_none()
    except MultipleResultsFound as exc:
        raise RepoAdminError(
            f"funder email matches more than one user: {email.strip()}"
        ) from exc
    if user is None:
        raise RepoAdminError(f"no user with email {email.strip()}")
    return user


async def _email_for(db: AsyncSession, user_id: object) -> str | None:
    if user_id is None:
        return None
    email = (await db.execute(select(User.email).where(User.id == user_id))).scalar_one_or_none()
    if not isinstance(email, str):
        return None
    return email


async def _installation_row(
    db: AsyncSession, installation_id: int
) -> GitHubInstallation | None:
    stmt = select(GitHubInstallation).where(
        GitHubInstallation.installation_id == installation_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_repo_links(db: AsyncSession) -> list[dict]:
    """Every repo link, including disabled ones and removed installations."""
    stmt = (
        select(GitHubRepoLink, GitHubInstallation, User)
        .outerjoin(
            GitHubInstallation,
            GitHubInstallation.installation_id == GitHubRepoLink.installation_id,
        )
        .outerjoin(User, User.id == GitHubRepoLink.funder_user_id)
        .order_by(func.lower(GitHubRepoLink.repo_full_name), GitHubRepoLink.repo_full_name)
    )
    listed = []
    for link, installation, user in (await db.execute(stmt)).all():
        policy = link.default_policy if isinstance(link.default_policy, dict) else {}
        listed.append(
            {
                "repo": link.repo_full_name,
                "installation_id": int(link.installation_id),
                "installation_removed": installation is None
                or installation.removed_at is not None,
                "enabled": bool(link.enabled),
                "strict_tests": _strict_tests_enabled(policy),
                "auto_merge_acceptable": bool(link.auto_merge_acceptable),
                "auto_dispatch_alerts": bool(link.auto_dispatch_alerts),
                "funder_email": user.email if user is not None else None,
                "required_checks": _checks_copy(link.required_checks),
                "allowed_paths": _plain_list(policy.get("allowed_paths")),
                "allowed_dependency_changes": _plain_list(
                    policy.get("allowed_dependency_changes")
                ),
                "onboarded_by_user_id": (
                    str(link.onboarded_by_user_id) if link.onboarded_by_user_id else None
                ),
                "onboarded_at": link.onboarded_at.isoformat() if link.onboarded_at else None,
            }
        )
    return listed


async def configure_repo_link(
    db: AsyncSession,
    repo: str,
    *,
    funder_email: str | None = None,
    checks: list | None = None,
    allowed_paths: list | None = None,
    allowed_dependency_changes: list | None = None,
    strict_tests: bool | None = None,
    auto_merge_acceptable: bool | None = None,
    auto_dispatch_alerts: bool | None = None,
) -> dict:
    """Update only the arguments that are not None.

    ``allowed_paths`` and ``allowed_dependency_changes`` replace those keys on
    ``default_policy`` and leave every other key in place. ``strict_tests``
    merges or removes the rules that make weakening existing tests a violation.
    Does not commit.
    """
    if (
        funder_email is None
        and checks is None
        and allowed_paths is None
        and allowed_dependency_changes is None
        and strict_tests is None
        and auto_merge_acceptable is None
        and auto_dispatch_alerts is None
    ):
        raise RepoAdminError("nothing to change")
    link = await _require_link(db, repo)
    new_user = await _require_user(db, funder_email) if funder_email is not None else None
    new_checks = _validated_checks(checks) if checks is not None else None
    new_paths = (
        _validated_strings(allowed_paths, "allowed path") if allowed_paths is not None else None
    )
    new_deps = (
        _validated_strings(allowed_dependency_changes, "allowed dependency")
        if allowed_dependency_changes is not None
        else None
    )
    _require_bool(strict_tests, "strict_tests")
    _require_bool(auto_merge_acceptable, "auto_merge_acceptable")
    _require_bool(auto_dispatch_alerts, "auto_dispatch_alerts")

    before: dict = {}
    after: dict = {}
    if new_user is not None:
        before["funder_email"] = await _email_for(db, link.funder_user_id)
        link.funder_user_id = new_user.id
        after["funder_email"] = new_user.email
    if new_checks is not None:
        before["required_checks"] = _checks_copy(link.required_checks)
        link.required_checks = new_checks
        after["required_checks"] = _checks_copy(new_checks)
    if new_paths is not None or new_deps is not None or strict_tests is not None:
        policy = _policy_dict(link.default_policy)
        if new_paths is not None:
            before["allowed_paths"] = _plain_list(policy.get("allowed_paths"))
            policy["allowed_paths"] = new_paths
            after["allowed_paths"] = list(new_paths)
        if new_deps is not None:
            before["allowed_dependency_changes"] = _plain_list(
                policy.get("allowed_dependency_changes")
            )
            policy["allowed_dependency_changes"] = new_deps
            after["allowed_dependency_changes"] = list(new_deps)
        if strict_tests is not None:
            before["strict_tests"] = _strict_tests_enabled(policy)
            _apply_strict_tests(policy, strict_tests)
            after["strict_tests"] = strict_tests
        link.default_policy = policy
    if auto_merge_acceptable is not None:
        before["auto_merge_acceptable"] = bool(link.auto_merge_acceptable)
        link.auto_merge_acceptable = auto_merge_acceptable
        after["auto_merge_acceptable"] = auto_merge_acceptable
    if auto_dispatch_alerts is not None:
        before["auto_dispatch_alerts"] = bool(link.auto_dispatch_alerts)
        link.auto_dispatch_alerts = auto_dispatch_alerts
        after["auto_dispatch_alerts"] = auto_dispatch_alerts
    await db.flush()
    return {"repo": link.repo_full_name, "before": before, "after": after}


async def _enable_problems(db: AsyncSession, link: GitHubRepoLink) -> list[str]:
    problems: list[str] = []
    if link.funder_user_id is None:
        problems.append("missing funder")
    if not _checks_are_configured(link.required_checks):
        problems.append("missing required check")
    installation = await _installation_row(db, int(link.installation_id))
    if installation is None:
        problems.append("missing installation")
    elif installation.removed_at is not None:
        problems.append("installation removed")
    if link.visibility not in _KNOWN_VISIBILITY:
        problems.append("repository visibility is unknown")
    return problems


async def set_repo_enabled(db: AsyncSession, repo: str, enabled: bool) -> dict:
    """Enable only with a funder, a required check, and a live installation.

    Disabling does not check those. Does not commit.
    """
    if not isinstance(enabled, bool):
        raise RepoAdminError("enabled must be true or false")
    link = await _require_link(db, repo)
    before = bool(link.enabled)
    if enabled:
        problems = await _enable_problems(db, link)
        if problems:
            raise RepoAdminError(f"cannot enable {link.repo_full_name}: {', '.join(problems)}")
    link.enabled = enabled
    await db.flush()
    return {
        "repo": link.repo_full_name,
        "before": {"enabled": before},
        "after": {"enabled": enabled},
    }
