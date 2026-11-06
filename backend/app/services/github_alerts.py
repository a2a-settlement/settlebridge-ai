"""Security alerts (Dependabot, code scanning) turned into bounty issues.

A dispatched issue's specs merge ``GitHubAlertDispatch.policy_overrides`` into the
repo's default policy, and pay only after GitHub marks the alert fixed.
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.github import (
    AlertKind,
    GitHubAlertDispatch,
    GitHubBountyBinding,
    GitHubJob,
    GitHubRepoLink,
    GitHubSettlement,
    JobKind,
    JobStatus,
    SettlementState,
)
from app.services.github_errors import PermanentJobError

logger = logging.getLogger(__name__)

ALERT_FIXED_TRIGGER = "merged_and_alert_fixed"
# Mediator rule_severity overrides that make weakening existing tests a violation.
STRICT_TEST_RULE_SEVERITY = {
    "test_file_deleted": "violation",
    "assertion_changed": "violation",
    "weak_assertion_added": "violation",
    "test_skip_added": "violation",
}

_ALERT_EVENTS = frozenset({"dependabot_alert", "code_scanning_alert"})
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# PEP 508 name. ASCII only, so the value cannot break out of a quoted command.
_PIP_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_PIP_VERSION_RE = re.compile(
    r"^(?:[0-9]+!)?"
    r"[0-9]+(?:\.[0-9]+)*"
    r"(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?"
    r"(?:\.dev[0-9]+)?"
    r"(?:\+[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)?$"
)
_NPM_NAME_RE = re.compile(
    r"^(?:@[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?/)?"
    r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$"
)
_NPM_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,5}$")
_PATH_RE = re.compile(r"^(?:[A-Za-z0-9_.@+-]+/)*[A-Za-z0-9_.@+-]+$")
_REQUIREMENTS_RE = re.compile(r"^requirements.*\.txt$")
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9_./:+-]{1,200}$")
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")
_GHSA_RE = re.compile(r"^GHSA(?:-[A-Za-z0-9]{4}){3}$")
_STATE_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_ECOSYSTEM_RE = re.compile(r"^[A-Za-z0-9_+-]{1,32}$")
_QUOTE_BREAKERS = set("\\\"'`$\n\r\t ")

_MANIFEST_LOCKS: dict[str, tuple[str, ...]] = {
    "pyproject.toml": ("poetry.lock", "uv.lock"),
    "package.json": ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
}
_LOCK_MANIFEST = {
    "poetry.lock": "pyproject.toml",
    "uv.lock": "pyproject.toml",
    "package-lock.json": "package.json",
    "yarn.lock": "package.json",
    "pnpm-lock.yaml": "package.json",
}
_WEEK = timedelta(days=7)
_DISPATCH_GRACE = timedelta(minutes=10)
_MARKER_SINCE_SKEW = timedelta(minutes=5)
_MAX_NAME_LEN = 214
_MAX_PATH_LEN = 400


@dataclass(frozen=True)
class _AlertPlan:
    kind: str
    number: int
    state: str | None
    usd: int
    title: str
    body: str
    labels: tuple[str, ...]
    policy_overrides: dict


async def handle_security_alert(db: AsyncSession, job: GitHubJob) -> None:
    """``dependabot_alert`` / ``code_scanning_alert``.

    ``created`` opens a bounty issue. ``fixed`` wakes the settlement waiting on that
    alert, even with dispatch turned off, so an already-open bounty still pays promptly.
    """
    from app.services import github_bounty_service

    try:
        event, action, data = github_bounty_service._webhook_parts(job)
    except PermanentJobError:
        if not settings.GITHUB_AUTO_DISPATCH_ALERTS:
            return
        raise
    if event not in _ALERT_EVENTS:
        return
    if action == "fixed":
        await _wake_settlement(db, event, data)
        return
    if action != "created" or not settings.GITHUB_AUTO_DISPATCH_ALERTS:
        return
    repo = github_bounty_service._repository_full_name(data)
    if _REPO_RE.fullmatch(repo) is None:
        raise PermanentJobError("webhook repository.full_name is invalid")
    link = await github_bounty_service.get_enabled_funded_repo_link(db, repo)
    if link is None or not link.auto_dispatch_alerts:
        return
    if event == "dependabot_alert":
        plan = _dependabot_plan(data, repo)
    else:
        plan = _code_scanning_plan(data, repo)
    if plan is None:
        return
    installation_id = github_bounty_service._installation_id(data)
    await _open_dispatch(db, data, repo, installation_id, plan)


async def _wake_settlement(db: AsyncSession, event: str, data: dict) -> None:
    """Move the waiting settlement's next alert poll to now. The poll still reads GitHub."""
    from app.services import github_bounty_service

    repo = github_bounty_service._repository_full_name(data)
    if _REPO_RE.fullmatch(repo) is None:
        raise PermanentJobError("webhook repository.full_name is invalid")
    number = _alert_number(_alert_dict(data))
    kind = (
        AlertKind.DEPENDABOT.value if event == "dependabot_alert" else AlertKind.CODE_SCANNING.value
    )
    dispatch = await _find_dispatch(db, repo, kind, number)
    if dispatch is None or dispatch.issue_number is None:
        return
    settlement = (
        await db.execute(
            select(GitHubSettlement)
            .join(GitHubBountyBinding, GitHubBountyBinding.id == GitHubSettlement.binding_id)
            .where(
                GitHubBountyBinding.repo_full_name == repo,
                GitHubBountyBinding.issue_number == dispatch.issue_number,
            )
        )
    ).scalar_one_or_none()
    if settlement is None or settlement.state != SettlementState.ESCROW_CREATED.value:
        return
    now = datetime.now(timezone.utc)
    jobs = (
        (
            await db.execute(
                select(GitHubJob).where(
                    GitHubJob.kind == JobKind.ADVANCE_SETTLEMENT.value,
                    GitHubJob.status == JobStatus.PENDING.value,
                )
            )
        )
        .scalars()
        .all()
    )
    woken = 0
    for pending in jobs:
        payload = pending.payload if isinstance(pending.payload, dict) else {}
        if payload.get("settlement_id") != str(settlement.id):
            continue
        due = _as_utc(pending.next_run_at)
        if due is not None and due > now:
            pending.next_run_at = now
            woken += 1
    if woken:
        await db.commit()
        logger.info(
            "%s alert %s#%s fixed; settlement %s rechecks now", kind, repo, number, settlement.id
        )


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


def _alert_dict(data: dict) -> dict:
    alert = data.get("alert")
    if not isinstance(alert, dict):
        raise PermanentJobError("webhook payload is missing alert")
    return alert


def _alert_number(alert: dict) -> int:
    from app.services import github_bounty_service

    return github_bounty_service._positive_int(alert.get("number"), "alert.number")


def _alert_state(alert: dict) -> str | None:
    state = alert.get("state")
    if isinstance(state, str) and _STATE_RE.fullmatch(state):
        return state
    return None


def _plain(value: object, limit: int) -> str:
    """One line of untrusted text, with fences neutralized so they cannot become the repro."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("```", "'''").split())
    return text[:limit]


def _quote_safe(value: str) -> bool:
    return bool(value) and all(
        char not in _QUOTE_BREAKERS and 32 < ord(char) < 127 for char in value
    )


def _safe_path(path: str) -> bool:
    if len(path) > _MAX_PATH_LEN or _PATH_RE.fullmatch(path) is None:
        return False
    return all(segment not in {".", ".."} for segment in path.split("/"))


def _join_path(directory: str, name: str) -> str:
    if directory:
        return f"{directory}/{name}"
    return name


def _lockfile_paths(manifest_path: object) -> list[str] | None:
    if not isinstance(manifest_path, str) or not _safe_path(manifest_path):
        return None
    directory, _, filename = manifest_path.rpartition("/")
    if filename in _LOCK_MANIFEST:
        return [manifest_path, _join_path(directory, _LOCK_MANIFEST[filename])]
    if _REQUIREMENTS_RE.fullmatch(filename):
        return [manifest_path]
    locks = _MANIFEST_LOCKS.get(filename, ())
    return [manifest_path, *[_join_path(directory, name) for name in locks]]


def _command_limit() -> int:
    from app.services.github_bounty_service import MAX_TEST_COMMAND_CHARS

    return MAX_TEST_COMMAND_CHARS


def _pip_command(normalized: str, patched: str) -> str | None:
    command = (
        'python -c "import importlib.metadata as m; '
        "from packaging.version import Version as V; "
        f"v=m.version('{normalized}'); assert V(v) >= V('{patched}'), v\""
    )
    if "\n" in command or len(command) > _command_limit():
        return None
    return command


def _npm_command(name: str, patched: str) -> str | None:
    # Left-to-right numeric compare (first difference wins), same idea as Version >=.
    script = (
        "const v=require('" + name + "/package.json').version.split('.').map(n=>parseInt(n,10));"
        "const p='" + patched + "'.split('.').map(n=>parseInt(n,10));"
        "for(let i=0;i<Math.max(v.length,p.length);i++){"
        "const a=v[i]||0,b=p[i]||0;if(a>b)break;if(a<b)throw new Error(v.join('.'))}"
    )
    command = 'node -e "' + script + '"'
    if "\n" in command or '"' in script or len(command) > _command_limit():
        return None
    return command


def _pip_dependency(name: object, patched: str) -> tuple[str, str] | None:
    if (
        not isinstance(name, str)
        or len(name) > _MAX_NAME_LEN
        or _PIP_NAME_RE.fullmatch(name) is None
        or not _quote_safe(name)
    ):
        return None
    if _PIP_VERSION_RE.fullmatch(patched) is None or not _quote_safe(patched):
        return None
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    if _PIP_NAME_RE.fullmatch(normalized) is None or not _quote_safe(normalized):
        return None
    command = _pip_command(normalized, patched)
    if command is None:
        return None
    return normalized, command


def _npm_dependency(name: object, patched: str) -> tuple[str, str] | None:
    if (
        not isinstance(name, str)
        or len(name) > _MAX_NAME_LEN
        or _NPM_NAME_RE.fullmatch(name) is None
        or not _quote_safe(name)
    ):
        return None
    if _NPM_VERSION_RE.fullmatch(patched) is None or not _quote_safe(patched):
        return None
    command = _npm_command(name, patched)
    if command is None:
        return None
    return name, command


def _patched_identifier(alert: dict) -> str | None:
    vulnerability = alert.get("security_vulnerability")
    if not isinstance(vulnerability, dict):
        return None
    patched = vulnerability.get("first_patched_version")
    if not isinstance(patched, dict):
        return None
    identifier = patched.get("identifier")
    if not isinstance(identifier, str) or identifier == "":
        return None
    return identifier


def _advisory(alert: dict) -> tuple[str | None, str | None]:
    advisory = alert.get("security_advisory")
    if not isinstance(advisory, dict):
        return None, None
    cve = advisory.get("cve_id")
    ghsa = advisory.get("ghsa_id")
    cve_ok = cve if isinstance(cve, str) and _CVE_RE.fullmatch(cve) else None
    ghsa_ok = ghsa if isinstance(ghsa, str) and _GHSA_RE.fullmatch(ghsa) else None
    label = cve_ok or ghsa_ok
    if ghsa_ok:
        return label, f"https://github.com/advisories/{ghsa_ok}"
    if cve_ok:
        return label, f"https://github.com/advisories?query={cve_ok}"
    return None, None


def _fit_title(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= 240:
        return flat
    return flat[:240].rstrip()


def _alert_marker(kind: str, number: int) -> str:
    return f"<!-- settlebridge:alert:{kind}:{number} -->"


def _dependabot_body(
    alert: dict, number: int, patched: str, command: str, label: str | None, link: str | None
) -> str:
    advisory = alert.get("security_advisory")
    summary = ""
    vulnerable = "unknown"
    if isinstance(advisory, dict):
        summary = _plain(advisory.get("summary"), 1000)
    vulnerability = alert.get("security_vulnerability")
    if isinstance(vulnerability, dict):
        vulnerable = _plain(vulnerability.get("vulnerable_version_range"), 200) or "unknown"
    lines: list[str] = []
    if summary:
        lines.append(summary)
        lines.append("")
    lines.append(f"Vulnerable range: {vulnerable}")
    lines.append(f"Patched version: {patched}")
    if label and link:
        lines.append(f"Advisory: {label} ({link})")
    elif label:
        lines.append(f"Advisory: {label}")
    lines.append("")
    lines.append("Reproduce:")
    lines.append("```")
    lines.append(command)
    lines.append("```")
    lines.append("")
    lines.append(
        "The fix must keep the test suite green and may only change the manifest and lockfile."
    )
    lines.append("")
    lines.append(_alert_marker(AlertKind.DEPENDABOT.value, number))
    return "\n".join(lines) + "\n"


def _dependabot_plan(data: dict, repo: str) -> _AlertPlan | None:
    alert = _alert_dict(data)
    number = _alert_number(alert)
    dependency = alert.get("dependency")
    if not isinstance(dependency, dict):
        raise PermanentJobError("dependabot alert is missing dependency")
    package = dependency.get("package")
    if not isinstance(package, dict):
        raise PermanentJobError("dependabot alert is missing dependency.package")
    ecosystem = package.get("ecosystem")
    if ecosystem not in {"pip", "npm"}:
        if isinstance(ecosystem, str) and _ECOSYSTEM_RE.fullmatch(ecosystem):
            shown = ecosystem
        else:
            shown = "?"
        logger.info(
            "skipping dependabot alert %s#%s: unsupported ecosystem %s",
            repo,
            number,
            shown,
        )
        return None
    patched = _patched_identifier(alert)
    if patched is None:
        logger.info("skipping dependabot alert %s#%s: no patched version", repo, number)
        return None
    paths = _lockfile_paths(dependency.get("manifest_path"))
    if paths is None:
        logger.info("skipping dependabot alert %s#%s: unsafe manifest path", repo, number)
        return None
    name = package.get("name")
    if ecosystem == "pip":
        built = _pip_dependency(name, patched)
    else:
        built = _npm_dependency(name, patched)
    if built is None:
        logger.info("skipping dependabot alert %s#%s: unsafe package name or version", repo, number)
        return None
    dep_name, command = built
    label, link = _advisory(alert)
    display = package.get("name")
    title = f"Security: update {display} to {patched}"
    if label:
        title = f"{title} ({label})"
    usd = int(settings.GITHUB_AUTO_BOUNTY_DEPENDABOT_USD)
    return _AlertPlan(
        kind=AlertKind.DEPENDABOT.value,
        number=number,
        state=_alert_state(alert),
        usd=usd,
        title=_fit_title(title),
        body=_dependabot_body(alert, number, patched, command, label, link),
        labels=(f"bounty: ${usd}",),
        policy_overrides={
            "allowed_paths": paths,
            "allowed_dependency_changes": [dep_name],
            "rule_severity": dict(STRICT_TEST_RULE_SEVERITY),
        },
    )


def _finding_location(alert: dict) -> str | None:
    instance = alert.get("most_recent_instance")
    if not isinstance(instance, dict):
        return None
    location = instance.get("location")
    if not isinstance(location, dict):
        return None
    path = location.get("path")
    if not isinstance(path, str) or not _safe_path(path):
        return None
    line = location.get("start_line")
    if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
        return path
    return f"{path}:{line}"


def _code_scanning_plan(data: dict, repo: str) -> _AlertPlan | None:
    alert = _alert_dict(data)
    number = _alert_number(alert)
    rule = alert.get("rule")
    if not isinstance(rule, dict):
        raise PermanentJobError("code scanning alert is missing rule")
    level = rule.get("security_severity_level")
    repository = data.get("repository")
    private = isinstance(repository, dict) and repository.get("private") is True
    # A public repo must not receive an issue that publishes an undisclosed security finding.
    if not private and level:
        logger.info(
            "skipping public code scanning alert %s#%s with a security severity",
            repo,
            number,
        )
        return None
    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or _RULE_ID_RE.fullmatch(rule_id) is None:
        logger.info("skipping code scanning alert %s#%s: unsafe rule id", repo, number)
        return None
    description = _plain(rule.get("description"), 2000)
    lines = [f"Rule: {rule_id}"]
    if description:
        lines.append(description)
    lines.append("")
    lines.append(f"Location: {_finding_location(alert) or 'unknown'}")
    lines.append("")
    lines.append(_alert_marker(AlertKind.CODE_SCANNING.value, number))
    usd = int(settings.GITHUB_AUTO_BOUNTY_CODEQL_USD)
    labels = [f"bounty: ${usd}"]
    if level:
        labels.append("tier: security")
    return _AlertPlan(
        kind=AlertKind.CODE_SCANNING.value,
        number=number,
        state=_alert_state(alert),
        usd=usd,
        title=_fit_title(f"Security: {rule_id}"),
        body="\n".join(lines) + "\n",
        labels=tuple(labels),
        policy_overrides={"rule_severity": dict(STRICT_TEST_RULE_SEVERITY)},
    )


def _as_utc(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _spent_this_week(db: AsyncSession, repo: str, now: datetime) -> int:
    cutoff = now - _WEEK
    stmt = select(GitHubAlertDispatch.amount_ate, GitHubAlertDispatch.created_at).where(
        GitHubAlertDispatch.repo_full_name == repo
    )
    spent = 0
    for amount, created in (await db.execute(stmt)).all():
        moment = _as_utc(created)
        if moment is not None and moment >= cutoff:
            spent += int(amount)
    return spent


async def _find_dispatch(
    db: AsyncSession, repo: str, kind: str, number: int
) -> GitHubAlertDispatch | None:
    stmt = select(GitHubAlertDispatch).where(
        GitHubAlertDispatch.repo_full_name == repo,
        GitHubAlertDispatch.alert_kind == kind,
        GitHubAlertDispatch.alert_number == number,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _issues_job(data: dict, issue_number: int, plan: _AlertPlan) -> SimpleNamespace:
    repository = data.get("repository")
    installation = data.get("installation")
    return SimpleNamespace(
        payload={
            "event": "issues",
            "action": "labeled",
            "data": {
                "issue": {
                    "number": issue_number,
                    "title": plan.title,
                    "body": plan.body,
                    "state": "open",
                    "labels": [{"name": label} for label in plan.labels],
                },
                "repository": copy.deepcopy(repository) if isinstance(repository, dict) else {},
                "installation": (
                    copy.deepcopy(installation) if isinstance(installation, dict) else {}
                ),
            },
        }
    )


async def _reconcile_stuck_dispatch(
    db: AsyncSession,
    data: dict,
    repo: str,
    installation_id: int,
    plan: _AlertPlan,
    existing: GitHubAlertDispatch,
) -> bool:
    """Adopt the issue for a stuck reservation, or drop an orphan.

    True means the row was removed and the caller should open an issue.
    A search failure leaves the row in place and raises.
    """
    from app.services import github_app, github_bounty_service

    created = _as_utc(existing.created_at)
    # GitHub may still be finishing the create that just timed out.
    if created is None or datetime.now(timezone.utc) - created < _DISPATCH_GRACE:
        logger.info(
            "alert dispatch %s %s#%s has no issue; not opening another",
            plan.kind,
            repo,
            plan.number,
        )
        return False
    try:
        # The issue can be timestamped slightly before the reservation commit.
        found = await github_app.find_issue_with_marker(
            installation_id,
            repo,
            _alert_marker(plan.kind, plan.number),
            since=created - _MARKER_SINCE_SKEW,
        )
    except Exception as exc:
        github_bounty_service._reraise_external(exc)
        raise
    if found is not None:
        existing.issue_number = int(found)
        await db.commit()
        logger.info(
            "recovered issue #%s for %s alert %s#%s",
            found,
            plan.kind,
            repo,
            plan.number,
        )
        await github_bounty_service._sync_issue_terms(db, _issues_job(data, int(found), plan))
        return False
    await db.delete(existing)
    await db.commit()
    logger.info("dropped orphaned alert dispatch %s %s#%s", plan.kind, repo, plan.number)
    return True


async def _open_dispatch(
    db: AsyncSession, data: dict, repo: str, installation_id: int, plan: _AlertPlan
) -> None:
    from app.services import github_app, github_bounty_service

    existing = await _find_dispatch(db, repo, plan.kind, plan.number)
    if existing is not None:
        if existing.issue_number is not None:
            return
        orphaned = await _reconcile_stuck_dispatch(db, data, repo, installation_id, plan, existing)
        if not orphaned:
            return
    amount_ate = github_bounty_service.usd_to_ate(Decimal(plan.usd))
    now = datetime.now(timezone.utc)
    # Serialize budget checks per repo; the lock is held until the reservation commits.
    await db.execute(
        select(GitHubRepoLink.repo_full_name)
        .where(GitHubRepoLink.repo_full_name == repo)
        .with_for_update()
    )
    spent = await _spent_this_week(db, repo, now)
    limit = int(settings.GITHUB_MAX_WEEKLY_AUTO_BOUNTY_ATE)
    if spent + amount_ate > limit:
        logger.info(
            "skipping %s alert %s#%s: weekly auto-bounty budget",
            plan.kind,
            repo,
            plan.number,
        )
        return
    # Reserve the row before GitHub so a redelivery cannot open a second issue.
    dispatch = GitHubAlertDispatch(
        id=uuid.uuid4(),
        repo_full_name=repo,
        alert_kind=plan.kind,
        alert_number=plan.number,
        issue_number=None,
        amount_ate=amount_ate,
        policy_overrides=plan.policy_overrides,
        alert_state=plan.state,
        created_at=now,
        updated_at=now,
    )
    db.add(dispatch)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.info("alert dispatch %s %s#%s already exists", plan.kind, repo, plan.number)
        return
    try:
        issue_number = await github_app.create_issue(
            installation_id, repo, plan.title, plan.body, list(plan.labels)
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        # A 4xx means GitHub opened no issue; free the row so a redelivery can retry.
        # Timeouts and 5xx may have opened one, so the row stays to avoid a duplicate.
        if isinstance(status, int) and 400 <= status < 500 and status != 429:
            await db.delete(dispatch)
            await db.commit()
        github_bounty_service._reraise_external(exc)
    dispatch.issue_number = int(issue_number)
    await db.commit()
    logger.info("opened issue #%s for %s alert %s#%s", issue_number, plan.kind, repo, plan.number)
    await github_bounty_service._sync_issue_terms(db, _issues_job(data, int(issue_number), plan))
