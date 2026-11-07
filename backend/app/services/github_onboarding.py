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
from app.services.github_bounty_service import (
    escrow_cost,
    sum_advertised_cost,
    visibility_from_repository,
)
from app.services.github_repo_admin import (
    RepoAdminError,
    _apply_strict_tests,
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
NOT_PACKAGE_DIRS = frozenset(
    {
        "build",
        "dist",
        "doc",
        "docs",
        "env",
        "example",
        "examples",
        "node_modules",
        "scripts",
        "venv",
    }
)
MAX_PACKAGE_PROBES = 10
_BOUNTY_COLOR = "0E8A16"
_TIER_COLOR = "5319E7"
_SECURITY_COLOR = "B60205"
STANDARD_LABELS: tuple[tuple[str, str, str], ...] = (
    *(
        (f"bounty: ${usd}", _BOUNTY_COLOR, f"SettleBridge bounty, ${usd} USD")
        for usd in (5, 10, 25, 50, 100)
    ),
    *(
        (f"tier: kya-{level}", _TIER_COLOR, f"SettleBridge: solver needs KYA level {level}+")
        for level in (2, 3)
    ),
    ("tier: security", _SECURITY_COLOR, "SettleBridge: security tier (KYA 2+, EMA 0.85+)"),
)
_KNOWN_VISIBILITY = frozenset({"public", "private", "internal"})
_FORBIDDEN_PROBES = (
    ".github/workflows/ci.yml",
    ".github/actions/setup/action.yml",
    ".github/CODEOWNERS",
    ".github/settlebridge.yml",
)


class OnboardError(RepoAdminError):
    """Onboarding cannot go ahead. ``code`` is stable for API clients; ``data`` adds detail."""

    code = "invalid_request"

    def __init__(self, message: str, *, code: str | None = None, **data):
        super().__init__(message)
        if code is not None:
            self.code = code
        self.data = data


class OnboardGitHubError(OnboardError):
    """GitHub could not be read while detecting the repo's defaults."""

    code = "github_unavailable"


class OnboardExchangeError(OnboardError):
    """The funder's exchange balance could not be read."""

    code = "exchange_unavailable"


class FunderConflictError(OnboardError):
    """The repo is already funded by a different user."""

    code = "funded_by_other"


def error_code(exc: RepoAdminError) -> str:
    return getattr(exc, "code", "invalid_request")


def error_data(exc: RepoAdminError) -> dict:
    return dict(getattr(exc, "data", {}))


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
    return ecosystem, sorted(f"{name}/*" for name in candidates if name in dirs)


def package_probe_dirs(entries: list[dict]) -> list[str]:
    """Root directories worth checking for an ``__init__.py``, at most ``MAX_PACKAGE_PROBES``."""
    names = sorted(
        entry["name"]
        for entry in entries
        if entry.get("type") == "dir"
        and entry["name"] not in PYTHON_DIRS
        and entry["name"] not in NOT_PACKAGE_DIRS
        and not entry["name"].startswith((".", "_"))
    )
    return names[:MAX_PACKAGE_PROBES]


def validated_allowed_paths(paths: object) -> list[str]:
    """Non-empty globs that cannot match CI configuration under ``.github/``."""
    cleaned = _validated_strings(paths, "allowed path")
    if not cleaned:
        raise OnboardError(
            "at least one allowed path is required; an empty list allows every path",
            code="needs_allowed_path",
        )
    for glob in cleaned:
        if any(fnmatch.fnmatch(probe, glob) for probe in _FORBIDDEN_PROBES):
            raise OnboardError(
                f"allowed path {glob!r} would allow changes under .github/",
                code="forbidden_path",
                path=glob,
            )
    return cleaned


async def _github(call):
    try:
        return await call
    except github_app.GitHubAPIError as exc:
        raise OnboardGitHubError(f"GitHub read failed: {exc}") from exc


async def detect_defaults(db: AsyncSession, repo: str) -> dict:
    """What onboarding would use for ``repo``, read from GitHub.

    Records repository visibility on the link. Does not change bounty settings.
    """
    link = await _require_link(db, repo)
    installation = await _installation_row(db, int(link.installation_id))
    if installation is None or installation.removed_at is not None:
        raise OnboardError(
            f"the GitHub App installation for {link.repo_full_name} is removed",
            code="app_not_installed",
        )
    installation_id = int(link.installation_id)
    name = link.repo_full_name
    repository = await _github(github_app.get_repository(installation_id, name))
    visibility = visibility_from_repository(repository)
    if visibility is not None and link.visibility != visibility:
        link.visibility = visibility
        await db.flush()
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
    package_dirs: list[str] = []
    if ecosystem == "python":
        for directory in package_probe_dirs(entries):
            children = await _github(
                github_app.list_root_entries(installation_id, name, branch_name, directory)
            )
            if any(c["name"] == "__init__.py" and c["type"] == "file" for c in children):
                package_dirs.append(directory)
        allowed_paths = sorted(set(allowed_paths) | {f"{d}/*" for d in package_dirs})
    raw_forking = repository.get("allow_forking")
    allow_forking = raw_forking if isinstance(raw_forking, bool) else None
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
        "package_dirs": package_dirs,
        "allowed_paths": allowed_paths,
        "allow_forking": allow_forking,
    }


async def ensure_standard_labels(installation_id: int, repo: str) -> dict:
    """Create missing ``STANDARD_LABELS``. Failures are reported, not raised."""
    report: dict[str, list] = {"created": [], "existing": [], "failed": []}
    for name, color, description in STANDARD_LABELS:
        try:
            outcome = await github_app.ensure_label(installation_id, repo, name, color, description)
        except github_app.GitHubAPIError as exc:
            report["failed"].append({"name": name, "status": exc.status_code})
            continue
        report["created" if outcome == "created" else "existing"].append(name)
    return report


def minimum_headroom() -> int:
    """ATE a funder needs free before onboarding: the escrow cost of a $1 bounty."""
    return escrow_cost(int(settings.GITHUB_ATE_PER_USD))


async def funder_headroom(db: AsyncSession, funder: User) -> int:
    """Free exchange balance after the funder's open bounties."""
    if not funder.exchange_bot_id:
        raise OnboardError(
            f"funder {funder.email} has no exchange account", code="funder_no_exchange"
        )
    try:
        dashboard = await asyncio.to_thread(exchange.get_balance_dashboard, funder.exchange_bot_id)
        available = int(dashboard["available"])
    except Exception as exc:
        raise OnboardExchangeError("could not read the funder's exchange balance") from exc
    return available - await sum_advertised_cost(db, funder.id)


def _format_checks(checks: list[dict]) -> str:
    return ", ".join(f"{check['name']}:{check['app_id']}" for check in checks) or "none"


def _option_changed(current: object, requested: bool | None) -> bool:
    if requested is None:
        return False
    if not isinstance(requested, bool):
        return True
    return bool(current) is not requested


def _severity_map(policy: dict) -> dict | None:
    severity = policy.get("rule_severity")
    if isinstance(severity, dict):
        return dict(severity)
    return None


def _strict_tests_differs(policy: dict, strict_tests: bool | None) -> bool:
    """Whether applying ``strict_tests`` would change ``rule_severity``. ``None`` would not."""
    if strict_tests is None:
        return False
    if not isinstance(strict_tests, bool):
        return True
    trial = _policy_dict(policy)
    _apply_strict_tests(trial, strict_tests)
    return _severity_map(trial) != _severity_map(policy)


async def onboard_repo(
    db: AsyncSession,
    repo: str,
    *,
    funder: User,
    checks: list | None = None,
    allowed_paths: list | None = None,
    allowed_dependency_changes: list | None = None,
    auto_dispatch_alerts: bool | None = None,
    auto_merge_acceptable: bool | None = None,
    strict_tests: bool | None = None,
    replace_funder: bool = False,
    actor_user_id=None,
    create_labels: bool = False,
) -> dict:
    """Configure and enable ``repo`` for ``funder`` in the caller's transaction.

    With ``create_labels``, also creates any missing ``STANDARD_LABELS`` on GitHub.
    That write is outside the transaction, so dry runs must leave it off.

    ``auto_dispatch_alerts``, ``auto_merge_acceptable`` and ``strict_tests`` update
    those repo options when passed. ``None`` leaves each one as it is.
    ``strict_tests`` merges or removes the rules that make weakening existing tests
    a violation, and leaves every other ``rule_severity`` entry in place.

    Passed values win over detected ones. Running it again with the same inputs
    returns ``changed: False`` and writes nothing.
    """
    detected = await detect_defaults(db, repo)
    link = await _require_link(db, repo)
    if link.visibility not in _KNOWN_VISIBILITY:
        raise OnboardError("repository visibility is unknown", code="visibility_unknown")
    if link.funder_user_id is not None and link.funder_user_id != funder.id and not replace_funder:
        raise FunderConflictError(f"{link.repo_full_name} is already funded by another user")

    if checks is not None:
        chosen_checks = _validated_checks(checks)
    else:
        chosen_checks = list(detected["required_checks"])
    if not chosen_checks:
        raise OnboardError(
            f"no required check is pinned to an app on {detected['default_branch']}; "
            "pass --check NAME:APP_ID. Successful checks on the latest commit: "
            f"{_format_checks(detected['candidate_checks'])}",
            code="no_pinned_check",
            candidates=detected["candidate_checks"],
        )
    chosen_paths = validated_allowed_paths(
        allowed_paths if allowed_paths is not None else detected["allowed_paths"]
    )
    chosen_deps = (
        _validated_strings(allowed_dependency_changes, "allowed dependency")
        if allowed_dependency_changes is not None
        else None
    )

    headroom = await funder_headroom(db, funder)
    minimum = minimum_headroom()
    if headroom < minimum:
        raise OnboardError(
            f"funder {funder.email} has {headroom} ATE free after open bounties; "
            f"a $1 bounty needs {minimum} ATE",
            code="funder_low_balance",
            headroom_ate=headroom,
            minimum_ate=minimum,
        )

    policy = _policy_dict(link.default_policy)
    changed = (
        link.funder_user_id != funder.id
        or _checks_copy(link.required_checks) != chosen_checks
        or sorted(map(str, _plain_list(policy.get("allowed_paths")))) != sorted(chosen_paths)
        or (
            chosen_deps is not None
            and _plain_list(policy.get("allowed_dependency_changes")) != chosen_deps
        )
        or not bool(link.enabled)
        or _option_changed(link.auto_dispatch_alerts, auto_dispatch_alerts)
        or _option_changed(link.auto_merge_acceptable, auto_merge_acceptable)
        or _strict_tests_differs(policy, strict_tests)
    )
    result = {
        "repo": link.repo_full_name,
        "detected": detected,
        "changed": changed,
        "funder_email": funder.email,
        "funder_headroom_ate": headroom,
        "before": {},
        "after": {},
        "labels": None,
    }
    if changed:
        configured = await configure_repo_link(
            db,
            link.repo_full_name,
            funder_email=funder.email,
            checks=chosen_checks,
            allowed_paths=chosen_paths,
            allowed_dependency_changes=chosen_deps,
            auto_dispatch_alerts=auto_dispatch_alerts,
            auto_merge_acceptable=auto_merge_acceptable,
            strict_tests=strict_tests,
        )
        enabled = await set_repo_enabled(db, link.repo_full_name, True)
        link.onboarded_by_user_id = actor_user_id if actor_user_id is not None else funder.id
        link.onboarded_at = _now()
        await db.flush()
        result["before"] = {**configured["before"], **enabled["before"]}
        result["after"] = {**configured["after"], **enabled["after"]}
    if create_labels:
        result["labels"] = await ensure_standard_labels(
            int(link.installation_id), link.repo_full_name
        )
    return result
