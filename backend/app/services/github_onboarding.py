"""Onboard a GitHub repo in one step: detect its checks and paths, then configure and enable it.

Detection only reads GitHub with the App's existing permissions. A required check
is applied without being passed in only when the default branch's rules or
protection pin it to an app id. Checks seen on recent runs are reported as
candidates and are never applied unless the caller passes them. Allowed paths
must be non-empty, because the mediator treats an empty list as "every path",
and must not match anything under ``.github/``.

``onboard_repo`` never commits. The caller commits or rolls back.
"""

from __future__ import annotations

import asyncio
import fnmatch
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.services import exchange, github_app
from app.services.github_bounty_service import escrow_cost, sum_advertised_cost
from app.services.github_repo_admin import (
    RepoAdminError,
    _checks_copy,
    _installation_row,
    _plain_list,
    _policy_dict,
    _require_link,
    _validated_checks,
    _validated_strings,
    configure_repo_link,
    set_repo_enabled,
)

PYTHON_MARKERS = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "pytest.ini"})
JS_MARKERS = frozenset({"package.json"})
PYTHON_DIRS = ("src", "app", "lib", "tests", "test")
JS_DIRS = ("src", "lib", "test", "tests")
_FORBIDDEN_PROBES = (
    ".github/workflows/ci.yml",
    ".github/actions/setup/action.yml",
    ".github/CODEOWNERS",
    ".github/settlebridge.yml",
)


class OnboardGitHubError(RepoAdminError):
    """GitHub could not be read while detecting the repo's defaults."""


class OnboardExchangeError(RepoAdminError):
    """The funder's exchange balance could not be read."""


class FunderConflictError(RepoAdminError):
    """The repo is already funded by a different user."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pinned_check(name: object, app_id: object) -> dict | None:
    if not isinstance(name, str) or name.strip() == "":
        return None
    if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
        return None
    return {"name": name.strip(), "app_id": app_id}


def _dedupe(checks: list[dict]) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    unique = []
    for check in checks:
        key = (check["name"], check["app_id"])
        if key not in seen:
            seen.add(key)
            unique.append(check)
    return unique


def checks_from_rules(rules: list[dict]) -> tuple[list[dict], list[str]]:
    """Pinned ``{name, app_id}`` checks and unpinned check names from branch rules."""
    pinned: list[dict] = []
    unpinned: list[str] = []
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters") if isinstance(rule.get("parameters"), dict) else {}
        for item in params.get("required_status_checks") or []:
            if not isinstance(item, dict):
                continue
            check = _pinned_check(item.get("context"), item.get("integration_id"))
            if check is not None:
                pinned.append(check)
            elif isinstance(item.get("context"), str) and item["context"].strip():
                unpinned.append(item["context"].strip())
    return _dedupe(pinned), sorted(set(unpinned))


def checks_from_protection(branch: dict) -> tuple[list[dict], list[str]]:
    """Pinned checks and unpinned names from classic branch protection, when visible."""
    protection = branch.get("protection") if isinstance(branch.get("protection"), dict) else {}
    status = protection.get("required_status_checks")
    if not isinstance(status, dict):
        return [], []
    pinned: list[dict] = []
    unpinned: list[str] = []
    for item in status.get("checks") or []:
        if not isinstance(item, dict):
            continue
        check = _pinned_check(item.get("context"), item.get("app_id"))
        if check is not None:
            pinned.append(check)
        elif isinstance(item.get("context"), str) and item["context"].strip():
            unpinned.append(item["context"].strip())
    for context in status.get("contexts") or []:
        if isinstance(context, str) and context.strip():
            unpinned.append(context.strip())
    pinned = _dedupe(pinned)
    pinned_names = {check["name"] for check in pinned}
    return pinned, sorted({name for name in unpinned if name not in pinned_names})


def candidate_checks(runs: list[dict]) -> list[dict]:
    """Successful check runs on a commit, as ``{name, app_id}``. Never applied by default."""
    found = []
    for run in runs:
        if not isinstance(run, dict) or run.get("conclusion") != "success":
            continue
        app = run.get("app") if isinstance(run.get("app"), dict) else {}
        check = _pinned_check(run.get("name"), app.get("id"))
        if check is not None:
            found.append(check)
    return sorted(_dedupe(found), key=lambda check: (check["name"], check["app_id"]))


def paths_for(entries: list[dict]) -> tuple[str | None, list[str]]:
    """The ecosystem and default allowed paths for a repo's root entries."""
    files = {entry["name"] for entry in entries if entry.get("type") == "file"}
    dirs = {entry["name"] for entry in entries if entry.get("type") == "dir"}
    if files & PYTHON_MARKERS:
        ecosystem, candidates = "python", PYTHON_DIRS
    elif files & JS_MARKERS:
        ecosystem, candidates = "javascript", JS_DIRS
    else:
        return None, []
    return ecosystem, [f"{name}/*" for name in candidates if name in dirs]


def validated_allowed_paths(paths: object) -> list[str]:
    """Non-empty globs that cannot match CI configuration under ``.github/``."""
    cleaned = _validated_strings(paths, "allowed path")
    if not cleaned:
        raise RepoAdminError(
            "at least one allowed path is required; an empty list allows every path"
        )
    for glob in cleaned:
        if any(fnmatch.fnmatch(probe, glob) for probe in _FORBIDDEN_PROBES):
            raise RepoAdminError(f"allowed path {glob!r} would allow changes under .github/")
    return cleaned


async def _github(call):
    try:
        return await call
    except github_app.GitHubAPIError as exc:
        raise OnboardGitHubError(f"GitHub read failed: {exc}") from exc


async def detect_defaults(db: AsyncSession, repo: str) -> dict:
    """What onboarding would use for ``repo``, read from GitHub. Does not write."""
    link = await _require_link(db, repo)
    installation = await _installation_row(db, int(link.installation_id))
    if installation is None or installation.removed_at is not None:
        raise RepoAdminError(f"the GitHub App installation for {link.repo_full_name} is removed")
    installation_id = int(link.installation_id)
    name = link.repo_full_name
    repository = await _github(github_app.get_repository(installation_id, name))
    branch_name = repository.get("default_branch")
    if not isinstance(branch_name, str) or branch_name == "":
        raise OnboardGitHubError(f"GitHub did not return a default branch for {name}")
    branch = await _github(github_app.get_branch(installation_id, name, branch_name))
    commit = branch.get("commit") if isinstance(branch.get("commit"), dict) else {}
    head_sha = commit.get("sha") if isinstance(commit.get("sha"), str) else None

    rules = await _github(github_app.get_branch_rules(installation_id, name, branch_name))
    pinned, unpinned = checks_from_rules(rules)
    source = "branch_rules" if pinned else None
    protected_pinned, protected_unpinned = checks_from_protection(branch)
    if not pinned and protected_pinned:
        pinned, source = protected_pinned, "branch_protection"
    unpinned = sorted(set(unpinned) | set(protected_unpinned))

    candidates: list[dict] = []
    if head_sha:
        runs = await _github(github_app.list_check_runs(installation_id, name, head_sha))
        candidates = candidate_checks(runs)
    entries = await _github(github_app.list_root_entries(installation_id, name, branch_name))
    ecosystem, allowed_paths = paths_for(entries)
    return {
        "repo": name,
        "installation_id": installation_id,
        "default_branch": branch_name,
        "head_sha": head_sha,
        "required_checks": pinned,
        "required_checks_source": source,
        "unpinned_required_checks": unpinned,
        "candidate_checks": candidates,
        "ecosystem": ecosystem,
        "allowed_paths": allowed_paths,
    }


async def _funder_headroom(db: AsyncSession, funder: User) -> int:
    if not funder.exchange_bot_id:
        raise RepoAdminError(f"funder {funder.email} has no exchange account")
    try:
        dashboard = await asyncio.to_thread(
            exchange.get_balance_dashboard, funder.exchange_bot_id
        )
        available = int(dashboard["available"])
    except Exception as exc:
        raise OnboardExchangeError("could not read the funder's exchange balance") from exc
    return available - await sum_advertised_cost(db, funder.id)


def _format_checks(checks: list[dict]) -> str:
    return ", ".join(f"{check['name']}:{check['app_id']}" for check in checks) or "none"


async def onboard_repo(
    db: AsyncSession,
    repo: str,
    *,
    funder: User,
    checks: list | None = None,
    allowed_paths: list | None = None,
    allowed_dependency_changes: list | None = None,
    replace_funder: bool = False,
    actor_user_id=None,
) -> dict:
    """Configure and enable ``repo`` for ``funder`` in the caller's transaction.

    Passed values win over detected ones. Running it again with the same inputs
    returns ``changed: False`` and writes nothing.
    """
    detected = await detect_defaults(db, repo)
    link = await _require_link(db, repo)
    if (
        link.funder_user_id is not None
        and link.funder_user_id != funder.id
        and not replace_funder
    ):
        raise FunderConflictError(f"{link.repo_full_name} is already funded by another user")

    if checks is not None:
        chosen_checks = _validated_checks(checks)
    else:
        chosen_checks = list(detected["required_checks"])
    if not chosen_checks:
        raise RepoAdminError(
            f"no required check is pinned to an app on {detected['default_branch']}; "
            "pass --check NAME:APP_ID. Successful checks on the latest commit: "
            f"{_format_checks(detected['candidate_checks'])}"
        )
    chosen_paths = validated_allowed_paths(
        allowed_paths if allowed_paths is not None else detected["allowed_paths"]
    )
    chosen_deps = (
        _validated_strings(allowed_dependency_changes, "allowed dependency")
        if allowed_dependency_changes is not None
        else None
    )

    headroom = await _funder_headroom(db, funder)
    minimum = escrow_cost(int(settings.GITHUB_ATE_PER_USD))
    if headroom < minimum:
        raise RepoAdminError(
            f"funder {funder.email} has {headroom} ATE free after open bounties; "
            f"a $1 bounty needs {minimum} ATE"
        )

    policy = _policy_dict(link.default_policy)
    changed = (
        link.funder_user_id != funder.id
        or _checks_copy(link.required_checks) != chosen_checks
        or _plain_list(policy.get("allowed_paths")) != chosen_paths
        or (
            chosen_deps is not None
            and _plain_list(policy.get("allowed_dependency_changes")) != chosen_deps
        )
        or not bool(link.enabled)
    )
    result = {
        "repo": link.repo_full_name,
        "detected": detected,
        "changed": changed,
        "funder_email": funder.email,
        "funder_headroom_ate": headroom,
        "before": {},
        "after": {},
    }
    if not changed:
        return result
    configured = await configure_repo_link(
        db,
        link.repo_full_name,
        funder_email=funder.email,
        checks=chosen_checks,
        allowed_paths=chosen_paths,
        allowed_dependency_changes=chosen_deps,
    )
    enabled = await set_repo_enabled(db, link.repo_full_name, True)
    link.onboarded_by_user_id = actor_user_id if actor_user_id is not None else funder.id
    link.onboarded_at = _now()
    await db.flush()
    result["before"] = {**configured["before"], **enabled["before"]}
    result["after"] = {**configured["after"], **enabled["after"]}
    return result
