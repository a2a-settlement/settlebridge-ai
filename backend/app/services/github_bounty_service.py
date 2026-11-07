"""GitHub bounty lifecycle: labels, frozen specs, evaluation, merge acceptance."""

from __future__ import annotations

import asyncio
import copy
import enum
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.bounty import Bounty, BountyMode, BountyStatus, Difficulty, ProvenanceTier
from app.models.github import (
    ACTIVE_LEASE_STATUSES,
    AcceptanceSpec,
    BindingState,
    FunderLedgerEntry,
    GitHubBountyBinding,
    GitHubInstallation,
    GitHubJob,
    GitHubLease,
    GitHubRepoLink,
    GitHubSettlement,
    JobKind,
    JobStatus,
    LeaseStatus,
    LedgerStatus,
    SettlementState,
)
from app.models.user import User
from app.services import exchange, github_app, github_comments, github_settlement, mediator
from app.services.github_errors import PermanentJobError, RetryableJobError

logger = logging.getLogger(__name__)

PAYMENT_TRIGGER = "merge_with_checks"
MAX_USD = Decimal("10000")
MAX_TEST_COMMAND_CHARS = 500
SECURITY_MIN_KYA = 2
SECURITY_MIN_EMA = 0.85
BALANCE_NOTICE = "balance checked; funds not reserved"
REASON_UNLINKED = "repository is not linked to an enabled funder"
REASON_INSUFFICIENT = f"insufficient checked balance; {BALANCE_NOTICE}"
REASON_INVALID_LABEL = "bounty label is invalid or conflicting"
REASON_NO_EXCHANGE = "funder has no exchange account for a balance check"
REASON_LABEL_REMOVED = "bounty label removed"
REASON_ISSUE_CLOSED = "issue closed"
REASON_INCREASE_REJECTED = (
    f"amount increase was not applied; insufficient checked balance; {BALANCE_NOTICE}; "
    "previous terms remain in force"
)
REASON_TERMS_UNCHANGED_UNLINKED = (
    "repository is not linked to an enabled funder; previous terms remain in force"
)
REASON_INVALID_LABEL_UNCHANGED = (
    "bounty label is invalid or conflicting; previous terms remain in force"
)
REASON_MERGED_SHA_MISMATCH = (
    "merged commit differs from evaluated commit; re-evaluation required"
)
REASON_STALE_SPEC = (
    "evaluation is not under the leased acceptance spec; re-evaluation required"
)
REASON_NOT_AWAITING_MERGE = "binding is not awaiting merge"
REASON_VIOLATION = "violation findings are present"
REASON_NO_LEASE_SUBMISSION = "no lease-bound submission"
REASON_HOLD_EXPIRED = "submitted hold has expired"
REASON_CHECKS_NOT_PASSING = (
    "required checks are not passing on the pull request's head commit"
)
REASON_SETTLEMENT_DISABLED = "settlement disabled"
REASON_NOT_PUBLIC = "repository is not public"
REASON_VISIBILITY_UNKNOWN = "repository visibility is unknown"
_REPOSITORY_VISIBILITIES = frozenset({"public", "private", "internal"})
_WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})
_SETTLED_STATES = frozenset(
    {BindingState.SETTLEMENT_PENDING.value, BindingState.PAID.value}
)
_PR_IGNORE_STATES = frozenset(
    {
        BindingState.SETTLEMENT_PENDING.value,
        BindingState.PAID.value,
        BindingState.CANCELLED.value,
    }
)
_KNOWN_OUTCOMES = {
    "ACCEPTABLE": BindingState.AWAITING_MERGE,
    "NEEDS_REVIEW": BindingState.NEEDS_REVIEW,
    "POLICY_VIOLATION": BindingState.ADVERTISED,
    "REJECTED_TOO_LARGE": BindingState.ADVERTISED,
    "REJECTED_CHECKS": BindingState.ADVERTISED,
}

_BOUNTY_LABEL_RE = re.compile(r"^\s*bounty\s*:\s*(.*?)\s*$", re.IGNORECASE)
_KYA_LABEL_RE = re.compile(r"^\s*tier\s*:\s*kya-(\d+)\s*$", re.IGNORECASE)
_SECURITY_LABEL_RE = re.compile(r"^\s*tier\s*:\s*security\s*$", re.IGNORECASE)
_PLAIN_USD_RE = re.compile(r"^\$\s*(\d+)(?:\.(\d+))?$")
_FENCE_RE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)```", re.MULTILINE | re.DOTALL)
_REPRO_LINE_RE = re.compile(r"^[ \t]*repro\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_CLOSING_RE = re.compile(
    r"\b(?:fixes|closes|resolves)"
    r"(?:[ \t]*:[ \t]*|[ \t]+)"
    r"(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<repo_issue>\d+)|#(?P<issue>\d+))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedLabels:
    amount_usd: Decimal | None
    kya_level: int | None
    security: bool


@dataclass(frozen=True)
class ChecksResult:
    state: str
    failed: list[str]


def _enum_value(value: object) -> str:
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


def _state_is(binding: GitHubBountyBinding, *states: BindingState) -> bool:
    current = _enum_value(binding.state)
    return current in {state.value for state in states}


def bounty_label_present(labels: list[str]) -> bool:
    return any(_BOUNTY_LABEL_RE.match(label) for label in labels)


def _parse_usd(raw: str) -> Decimal | None:
    body = raw.strip()
    if re.search(r"[eE]", body):
        return None
    match = _PLAIN_USD_RE.fullmatch(body)
    if match is None:
        return None
    fraction = match.group(2)
    if fraction is not None and len(fraction) > 2:
        return None
    whole = match.group(1)
    amount = Decimal(whole if fraction is None else f"{whole}.{fraction}")
    if amount <= 0 or amount > MAX_USD:
        return None
    return amount


def parse_bounty_labels(labels: list[str]) -> ParsedLabels:
    amounts: list[Decimal] = []
    invalid_amount = False
    saw_bounty = False
    kya_levels: list[int] = []
    security = False
    for label in labels:
        bounty_match = _BOUNTY_LABEL_RE.match(label)
        if bounty_match:
            saw_bounty = True
            parsed = _parse_usd(bounty_match.group(1))
            if parsed is None:
                invalid_amount = True
            else:
                amounts.append(parsed)
            continue
        kya_match = _KYA_LABEL_RE.match(label)
        if kya_match:
            level = int(kya_match.group(1))
            if 0 <= level <= 5:
                kya_levels.append(level)
            continue
        if _SECURITY_LABEL_RE.match(label):
            security = True
    if not saw_bounty or invalid_amount or len(set(amounts)) > 1:
        amount: Decimal | None = None
    else:
        amount = amounts[0] if amounts else None
    kya = max(kya_levels) if kya_levels else None
    return ParsedLabels(amount_usd=amount, kya_level=kya, security=security)


def extract_test_command(issue_body: str | None) -> str | None:
    if not issue_body:
        return None
    lines = issue_body.splitlines()
    for index, line in enumerate(lines):
        if "reproduce" not in line.lower():
            continue
        rest = "\n".join(lines[index + 1 :])
        for match in _FENCE_RE.finditer(rest):
            command = match.group(1).strip()
            if command:
                return command[:MAX_TEST_COMMAND_CHARS]
        break
    match = _REPRO_LINE_RE.search(issue_body)
    if match is None:
        return None
    command = match.group(1).strip()
    if not command:
        return None
    return command[:MAX_TEST_COMMAND_CHARS]


def usd_to_ate(amount: Decimal) -> int:
    rate = Decimal(str(settings.GITHUB_ATE_PER_USD))
    quantized = (amount * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(quantized)


def effective_requirements(
    parsed: ParsedLabels, defaults: tuple[int, float]
) -> tuple[int, float]:
    default_kya, default_ema = defaults
    kya = default_kya if parsed.kya_level is None else parsed.kya_level
    ema = float(default_ema)
    if parsed.security:
        kya = max(int(kya), SECURITY_MIN_KYA)
        ema = max(ema, SECURITY_MIN_EMA)
    return int(kya), ema


def default_required_ema(repo_full_name: str) -> float:
    """The repo's GITHUB_REPO_REQUIRED_EMA entry, else GITHUB_DEFAULT_REQUIRED_EMA."""
    for entry in settings.GITHUB_REPO_REQUIRED_EMA.split(","):
        repo, sep, value = entry.strip().partition("=")
        if not sep or repo.strip().lower() != repo_full_name.lower():
            continue
        try:
            ema = float(value)
        except ValueError:
            ema = -1.0
        if 0.0 <= ema <= 1.0:
            return ema
        logger.error("ignoring invalid GITHUB_REPO_REQUIRED_EMA entry for %s", repo_full_name)
    return float(settings.GITHUB_DEFAULT_REQUIRED_EMA)


def build_spec_content(
    *,
    test_command: str | None,
    required_checks: list,
    policy: dict,
    payment_trigger: str,
    amount_ate: int,
    fee_bps: int,
    required_kya: int,
    required_ema: float,
) -> dict:
    return {
        "test_command": test_command,
        "required_checks": required_checks,
        "policy": policy,
        "payment_trigger": payment_trigger,
        "amount_ate": amount_ate,
        "fee_bps": fee_bps,
        "required_kya": required_kya,
        "required_ema": required_ema,
    }


def spec_content_hash(content: dict) -> str:
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def format_usd(amount: Decimal) -> str:
    return format(amount, "f")


def ledger_allows(open_advertised_total: int, new_amount: int, available_balance: int) -> bool:
    return open_advertised_total + new_amount <= available_balance


def escrow_cost(amount_ate: int) -> int:
    """What the funder's balance pays for an escrow: the amount plus the exchange fee."""
    if amount_ate <= 0:
        return 0
    pct = Decimal(settings.GITHUB_EXCHANGE_FEE_PERCENT) / Decimal("100")
    fee = (Decimal(amount_ate) * pct).to_integral_value(rounding=ROUND_CEILING)
    return amount_ate + max(int(fee), int(settings.GITHUB_EXCHANGE_MIN_FEE))


def _run_sort_key(run: dict) -> tuple[str, str, int]:
    """Most recent attempt: completion time, otherwise start time, then id."""
    completed = str(run.get("completed_at") or "")
    started = str(run.get("started_at") or "")
    try:
        numeric_id = int(run.get("id") or 0)
    except (TypeError, ValueError):
        numeric_id = 0
    return (completed or started, started, numeric_id)


def _run_matches(run: dict, name: object, app_id: object, head_sha: str) -> bool:
    if run.get("name") != name or run.get("head_sha") != head_sha:
        return False
    app = run.get("app")
    if not isinstance(app, dict):
        return False
    return app.get("id") == app_id


def evaluate_required_checks(
    required: list[dict] | None, runs: list[dict], head_sha: str
) -> ChecksResult:
    if not required:
        return ChecksResult(state="unconfigured", failed=[])
    failed: list[str] = []
    pending = False
    for item in required:
        if not isinstance(item, dict) or "name" not in item or "app_id" not in item:
            pending = True
            continue
        name = item["name"]
        matches = [
            run
            for run in runs
            if isinstance(run, dict) and _run_matches(run, name, item["app_id"], head_sha)
        ]
        if not matches:
            pending = True
            continue
        latest = max(matches, key=_run_sort_key)
        if latest.get("status") != "completed":
            pending = True
            continue
        if latest.get("conclusion") != "success":
            failed.append(str(name))
    if failed:
        return ChecksResult(state="failed", failed=failed)
    if pending:
        return ChecksResult(state="pending", failed=[])
    return ChecksResult(state="passed", failed=[])


def outcome_to_state(outcome: str) -> BindingState:
    return _KNOWN_OUTCOMES.get(outcome, BindingState.ADVERTISED)


def _finding_list(value: object) -> list:
    if isinstance(value, list):
        return value
    return []


def _has_violation(assessment: object) -> bool:
    if not isinstance(assessment, dict):
        return False
    return any(
        isinstance(item, dict) and item.get("severity") == "violation"
        for item in _finding_list(assessment.get("findings"))
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hold_expired(hold_expires_at: object) -> bool:
    """True when a hold is set and strictly before now. Naive datetimes are UTC."""
    if not isinstance(hold_expires_at, datetime):
        return False
    return _as_utc(hold_expires_at) < _as_utc(datetime.now(timezone.utc))


def merge_acceptance_decision(binding, merged_head_sha: str, lease) -> tuple[bool, str]:
    """Accept a merge only for the submitted lease's pinned spec.

    ``lease`` may be None. Label edits change ``current_spec_id`` for future
    leases and do not invalidate an evaluation pinned to ``lease.spec_id``.
    """
    if (
        lease is None
        or _enum_value(lease.status) != LeaseStatus.SUBMITTED.value
        or lease.pr_number != binding.pr_number
    ):
        return False, REASON_NO_LEASE_SUBMISSION
    if _hold_expired(getattr(lease, "hold_expires_at", None)):
        return False, REASON_HOLD_EXPIRED
    if _enum_value(binding.state) != BindingState.AWAITING_MERGE.value:
        return False, REASON_NOT_AWAITING_MERGE
    evaluated = binding.evaluated_head_sha
    if not evaluated or merged_head_sha != evaluated:
        return False, REASON_MERGED_SHA_MISMATCH
    if binding.evaluated_spec_id is None or binding.evaluated_spec_id != lease.spec_id:
        return False, REASON_STALE_SPEC
    if _has_violation(binding.last_assessment):
        return False, REASON_VIOLATION
    return True, "accepted"


def parse_closing_reference(pr_body: str | None, repo: str) -> int | None:
    if not pr_body:
        return None
    wanted = repo.casefold()
    for match in _CLOSING_RE.finditer(pr_body):
        if match.group("repo"):
            if match.group("repo").casefold() == wanted:
                return int(match.group("repo_issue"))
            continue
        return int(match.group("issue"))
    return None


def _reraise_external(exc: Exception) -> None:
    if isinstance(exc, (RetryableJobError, PermanentJobError)):
        raise exc
    if isinstance(exc, github_app.GitHubAPIError):
        code = exc.status_code
        if code is None or code == 429 or code >= 500:
            raise RetryableJobError(str(exc)) from exc
        raise PermanentJobError(str(exc)) from exc
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429 or code >= 500:
            raise RetryableJobError(str(exc)) from exc
        raise PermanentJobError(str(exc)) from exc
    if isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError)):
        raise RetryableJobError(str(exc)) from exc
    if isinstance(exc, ValueError):
        raise PermanentJobError(str(exc)) from exc
    raise exc


async def _github_call(awaitable):
    try:
        return await awaitable
    except Exception as exc:
        _reraise_external(exc)


async def _flush(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        raise RetryableJobError("concurrent GitHub bounty write") from exc


def _with_balance_notice(body: str) -> str:
    if "funds not reserved" in body.lower():
        return body
    return body.rstrip() + "\n\n" + BALANCE_NOTICE


def repo_issue_marker(repo: str, issue_number: int) -> str:
    return f"<!-- settlebridge:repo:{repo}#{issue_number} -->"


async def _sticky(
    installation_id: int,
    repo: str,
    issue_number: int,
    marker: str,
    body: str,
    comment_id: int | None,
    *,
    balance_notice: bool = False,
) -> int:
    if balance_notice:
        body = _with_balance_notice(body)
    try:
        return await github_app.upsert_sticky_comment(
            installation_id, repo, issue_number, marker, body, comment_id=comment_id
        )
    except Exception as exc:
        _reraise_external(exc)
        raise


def _webhook_parts(job: GitHubJob) -> tuple[str | None, str | None, dict]:
    payload = job.payload
    if not isinstance(payload, dict):
        raise PermanentJobError("job payload must be an object")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PermanentJobError("job payload is missing webhook data")
    event = payload.get("event")
    action = payload.get("action")
    if action is None and isinstance(data.get("action"), str):
        action = data.get("action")
    if event is not None and not isinstance(event, str):
        raise PermanentJobError("job event must be a string")
    if action is not None and not isinstance(action, str):
        raise PermanentJobError("job action must be a string")
    return event, action, data


def _installation_id(data: dict) -> int:
    installation = data.get("installation")
    if not isinstance(installation, dict) or "id" not in installation:
        raise PermanentJobError("webhook payload is missing installation.id")
    try:
        return int(installation["id"])
    except (TypeError, ValueError) as exc:
        raise PermanentJobError("installation.id is not an integer") from exc


def _repository_full_name(data: dict) -> str:
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise PermanentJobError("webhook payload is missing repository")
    full_name = repository.get("full_name")
    if not isinstance(full_name, str) or "/" not in full_name:
        raise PermanentJobError("webhook payload is missing repository.full_name")
    return full_name


def _positive_int(value: object, field: str) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PermanentJobError(f"webhook payload is missing {field}") from exc
    if number <= 0:
        raise PermanentJobError(f"{field} must be positive")
    return number


def _label_names(issue: dict) -> list[str]:
    names: list[str] = []
    labels = issue.get("labels") or []
    if not isinstance(labels, list):
        raise PermanentJobError("issue labels must be a list")
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
    return names


def visibility_from_repository(repository: object) -> str | None:
    """Visibility from a GitHub repository object.

    ``visibility`` wins when it is public, private, or internal. Otherwise the
    boolean ``private`` flag is used. Internal repositories report ``private: true``.
    """
    if not isinstance(repository, dict):
        return None
    visibility = repository.get("visibility")
    if isinstance(visibility, str) and visibility in _REPOSITORY_VISIBILITIES:
        return visibility
    private = repository.get("private")
    if private is True:
        return "private"
    if private is False:
        return "public"
    return None


def _apply_known_visibility(link, repository: object) -> None:
    if link is None:
        return
    visibility = visibility_from_repository(repository)
    if visibility is not None:
        link.visibility = visibility


async def _record_repository_visibility(
    db: AsyncSession, repo: str, repository: object
) -> None:
    """Store visibility from a webhook repository object when it names one."""
    if visibility_from_repository(repository) is None:
        return
    _apply_known_visibility(await get_repo_link(db, repo), repository)


async def _ensure_visibility_known(link, installation_id: int, repo: str) -> bool:
    """Refresh an unknown link once. True when visibility is public, private, or internal.

    A GitHub failure raises through ``_github_call`` and does not mark the repo public.
    """
    if getattr(link, "visibility", None) in _REPOSITORY_VISIBILITIES:
        return True
    payload = await _github_call(github_app.get_repository(installation_id, repo))
    link.visibility = visibility_from_repository(payload)
    return getattr(link, "visibility", None) in _REPOSITORY_VISIBILITIES


def _repo_records(entries: object) -> list[tuple[str, dict | None]]:
    if not isinstance(entries, list):
        return []
    records: list[tuple[str, dict | None]] = []
    for entry in entries:
        if isinstance(entry, str) and "/" in entry:
            records.append((entry, None))
        elif isinstance(entry, dict) and isinstance(entry.get("full_name"), str):
            records.append((entry["full_name"], entry))
    return records


def _repo_names(entries: object) -> list[str]:
    return [full_name for full_name, _repository in _repo_records(entries)]


def _account_login(data: dict) -> str:
    installation = data.get("installation")
    account = installation.get("account") if isinstance(installation, dict) else None
    login = account.get("login") if isinstance(account, dict) else None
    if not isinstance(login, str) or not login:
        raise PermanentJobError("installation account login is missing")
    return login


def _pull_request_numbers(suite: dict) -> list[int]:
    numbers: list[int] = []
    pull_requests = suite.get("pull_requests") or []
    if not isinstance(pull_requests, list):
        raise PermanentJobError("check suite pull_requests must be a list")
    for pull_request in pull_requests:
        if isinstance(pull_request, dict) and pull_request.get("number") is not None:
            numbers.append(_positive_int(pull_request.get("number"), "pull_request.number"))
    return numbers


def _as_dict(value: object) -> dict:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return copy.deepcopy(value)
    return []


async def get_installation(db: AsyncSession, installation_id: int) -> GitHubInstallation | None:
    stmt = select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_repo_links_for_installation(
    db: AsyncSession, installation_id: int
) -> list[GitHubRepoLink]:
    stmt = select(GitHubRepoLink).where(GitHubRepoLink.installation_id == installation_id)
    return list((await db.execute(stmt)).scalars().all())


async def get_repo_link(db: AsyncSession, repo_full_name: str) -> GitHubRepoLink | None:
    stmt = select(GitHubRepoLink).where(GitHubRepoLink.repo_full_name == repo_full_name)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_enabled_funded_repo_link(
    db: AsyncSession, repo_full_name: str
) -> GitHubRepoLink | None:
    stmt = select(GitHubRepoLink).where(
        GitHubRepoLink.repo_full_name == repo_full_name,
        GitHubRepoLink.enabled.is_(True),
        GitHubRepoLink.funder_user_id.is_not(None),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_binding_by_issue(
    db: AsyncSession, repo_full_name: str, issue_number: int
) -> GitHubBountyBinding | None:
    stmt = select(GitHubBountyBinding).where(
        GitHubBountyBinding.repo_full_name == repo_full_name,
        GitHubBountyBinding.issue_number == issue_number,
    )
    return await _locked_binding(db, stmt)


async def get_binding_by_pull_request(
    db: AsyncSession, repo_full_name: str, pr_number: int
) -> GitHubBountyBinding | None:
    stmt = select(GitHubBountyBinding).where(
        GitHubBountyBinding.repo_full_name == repo_full_name,
        GitHubBountyBinding.pr_number == pr_number,
    )
    return await _locked_binding(db, stmt)


async def _locked_binding(db: AsyncSession, stmt) -> GitHubBountyBinding | None:
    # Every web worker runs a job loop, so a merge and the issue close it
    # triggers are handled concurrently. The row lock makes the later handler
    # wait for the earlier commit, and populate_existing makes it see that commit.
    stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_bounty(db: AsyncSession, bounty_id: uuid.UUID) -> Bounty | None:
    stmt = select(Bounty).where(Bounty.id == bounty_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_acceptance_spec(db: AsyncSession, spec_id: uuid.UUID) -> AcceptanceSpec | None:
    stmt = select(AcceptanceSpec).where(AcceptanceSpec.id == spec_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def lock_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    stmt = select(User).where(User.id == user_id).with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def sum_advertised_cost(db: AsyncSession, funder_user_id: uuid.UUID) -> int:
    """Lock advertised ledger rows and total their escrow costs. This is not a fund reservation."""
    stmt = (
        select(FunderLedgerEntry)
        .where(
            FunderLedgerEntry.funder_user_id == funder_user_id,
            FunderLedgerEntry.status == LedgerStatus.ADVERTISED.value,
        )
        .with_for_update()
    )
    rows = (await db.execute(stmt)).scalars().all()
    return sum(escrow_cost(int(row.amount_ate)) for row in rows)


async def get_ledger_entry(db: AsyncSession, binding_id: uuid.UUID) -> FunderLedgerEntry | None:
    stmt = (
        select(FunderLedgerEntry)
        .where(FunderLedgerEntry.binding_id == binding_id)
        .with_for_update()
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_settlement_for_binding(
    db: AsyncSession, binding_id: uuid.UUID
) -> GitHubSettlement | None:
    stmt = select(GitHubSettlement).where(GitHubSettlement.binding_id == binding_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_submitted_lease(db: AsyncSession, binding_id: uuid.UUID) -> GitHubLease | None:
    stmt = select(GitHubLease).where(
        GitHubLease.binding_id == binding_id,
        GitHubLease.status == LeaseStatus.SUBMITTED.value,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_active_lease(db: AsyncSession, binding_id: uuid.UUID) -> GitHubLease | None:
    """The work or submitted lease, if any. A binding has at most one."""
    stmt = select(GitHubLease).where(
        GitHubLease.binding_id == binding_id,
        GitHubLease.status.in_(ACTIVE_LEASE_STATUSES),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _build_issue_content(
    parsed: ParsedLabels, issue_body: str | None, link, dispatch=None
) -> dict:
    """``dispatch`` is the issue's GitHubAlertDispatch, if a security alert created it."""
    from app.services import github_alerts

    amount = parsed.amount_usd
    if amount is None:
        raise PermanentJobError("cannot freeze a bounty without an amount")
    kya, ema = effective_requirements(
        parsed,
        (settings.GITHUB_DEFAULT_KYA_LEVEL, default_required_ema(link.repo_full_name)),
    )
    return build_spec_content(
        test_command=extract_test_command(issue_body if isinstance(issue_body, str) else None),
        required_checks=_as_list(link.required_checks),
        policy=(
            github_alerts.merge_policy(
                _as_dict(link.default_policy), _as_dict(dispatch.policy_overrides)
            )
            if dispatch is not None
            else _as_dict(link.default_policy)
        ),
        payment_trigger=(
            github_alerts.ALERT_FIXED_TRIGGER if dispatch is not None else PAYMENT_TRIGGER
        ),
        amount_ate=usd_to_ate(amount),
        fee_bps=int(settings.GITHUB_CLEARING_FEE_BPS),
        required_kya=kya,
        required_ema=ema,
    )


def _stage_spec(
    db: AsyncSession,
    binding_id: uuid.UUID,
    version: int,
    content: dict,
    content_hash: str,
    *,
    security_tier: bool = False,
) -> AcceptanceSpec:
    # Acceptance terms are insert-only. A change is a new version, never an update.
    # security_tier follows ParsedLabels.security and is not part of the content hash.
    spec = AcceptanceSpec(
        id=uuid.uuid4(),
        binding_id=binding_id,
        version=version,
        content_hash=content_hash,
        test_command=content["test_command"],
        required_checks=content["required_checks"],
        policy=content["policy"],
        payment_trigger=content["payment_trigger"],
        amount_ate=content["amount_ate"],
        fee_bps=content["fee_bps"],
        required_kya=content["required_kya"],
        required_ema=content["required_ema"],
        security_tier=security_tier,
    )
    db.add(spec)
    return spec


async def _comment_not_advertised(
    installation_id: int,
    repo: str,
    issue_number: int,
    reason: str,
    *,
    binding: GitHubBountyBinding | None = None,
    balance_notice: bool = False,
) -> None:
    body = github_comments.render_not_advertised(reason=reason)
    if binding is None:
        marker = repo_issue_marker(repo, issue_number)
        comment_id = None
    else:
        marker = github_comments.bounty_marker(binding.bounty_id)
        comment_id = binding.comment_id
    new_id = await _sticky(
        installation_id,
        repo,
        issue_number,
        marker,
        body,
        comment_id,
        balance_notice=balance_notice,
    )
    if binding is not None:
        binding.comment_id = new_id


async def _refresh_advertised_comment(
    binding: GitHubBountyBinding,
    spec: AcceptanceSpec,
    amount_usd: str,
    installation_id: int,
) -> None:
    body = github_comments.render_advertised(
        bounty_id=binding.bounty_id,
        amount_ate=spec.amount_ate,
        amount_usd=amount_usd,
        spec_version=spec.version,
        spec_hash=spec.content_hash,
        required_kya=spec.required_kya,
        required_ema=spec.required_ema,
        required_checks=_as_list(spec.required_checks),
        test_command=spec.test_command,
        market_url=settings.MARKET_URL,
    )
    marker = github_comments.bounty_marker(binding.bounty_id)
    binding.comment_id = await _sticky(
        installation_id,
        binding.repo_full_name,
        binding.issue_number,
        marker,
        body,
        binding.comment_id,
        balance_notice=True,
    )


async def _post_evaluation(
    binding: GitHubBountyBinding,
    installation_id: int,
    *,
    outcome: str,
    head_sha: str,
    spec_version: int,
    findings: list | None = None,
    failed_checks: list[str] | None = None,
    reviewer_summary: str | None = None,
) -> None:
    body = github_comments.render_evaluation(
        bounty_id=binding.bounty_id,
        outcome=outcome,
        head_sha=head_sha,
        spec_version=spec_version,
        findings=findings or [],
        failed_checks=failed_checks,
        reviewer_summary=reviewer_summary,
    )
    marker = github_comments.bounty_marker(binding.bounty_id)
    binding.comment_id = await _sticky(
        installation_id,
        binding.repo_full_name,
        binding.issue_number,
        marker,
        body,
        binding.comment_id,
    )


def _clear_evaluation(binding: GitHubBountyBinding) -> None:
    binding.evaluated_head_sha = None
    binding.evaluated_spec_id = None
    binding.last_assessment = None
    binding.review_cleared_by = None


def _unbind_pull_request(binding: GitHubBountyBinding) -> None:
    binding.pr_number = None
    binding.lease_holder_agent_id = None
    _clear_evaluation(binding)


def _release_lease(db: AsyncSession, lease, reason: str) -> None:
    from app.services.github_leases import enqueue_hold_refund

    lease.status = LeaseStatus.RELEASED.value
    lease.release_reason = reason
    enqueue_hold_refund(db, lease)


def _bounty_status(bounty) -> str:
    return _enum_value(bounty.status)


async def _cancel_binding(
    db: AsyncSession,
    binding: GitHubBountyBinding,
    installation_id: int,
    reason: str,
) -> None:
    if _state_is(binding, BindingState.SETTLEMENT_PENDING, BindingState.PAID):
        return
    lease = await get_active_lease(db, binding.id)
    if lease is not None:
        submitted = _enum_value(lease.status) == LeaseStatus.SUBMITTED.value
        _release_lease(db, lease, "bounty_cancelled")
        if submitted:
            _unbind_pull_request(binding)
    if not _state_is(binding, BindingState.CANCELLED):
        binding.state = BindingState.CANCELLED.value
    ledger = await get_ledger_entry(db, binding.id)
    if ledger is not None and _enum_value(ledger.status) == LedgerStatus.ADVERTISED.value:
        ledger.status = LedgerStatus.RELEASED.value
    bounty = await get_bounty(db, binding.bounty_id)
    if bounty is not None and _bounty_status(bounty) not in {
        BountyStatus.CANCELLED.value,
        BountyStatus.COMPLETED.value,
    }:
        bounty.status = BountyStatus.CANCELLED
    body = github_comments.render_cancelled(bounty_id=binding.bounty_id, reason=reason)
    marker = github_comments.bounty_marker(binding.bounty_id)
    binding.comment_id = await _sticky(
        installation_id,
        binding.repo_full_name,
        binding.issue_number,
        marker,
        body,
        binding.comment_id,
    )
    await _flush(db)


async def _checked_available(account_id: str) -> int:
    try:
        dashboard = await asyncio.to_thread(exchange.get_balance_dashboard, account_id)
    except Exception as exc:
        _reraise_external(exc)
        raise
    if not isinstance(dashboard, dict) or "available" not in dashboard:
        raise PermanentJobError("exchange balance response is missing available")
    try:
        return int(dashboard["available"])
    except (TypeError, ValueError) as exc:
        raise PermanentJobError("exchange available balance is not an integer") from exc


async def _create_advertised_bounty(
    db: AsyncSession,
    *,
    installation_id: int,
    repo: str,
    issue_number: int,
    issue: dict,
    link,
    content: dict,
    content_hash: str,
    amount_usd: str,
    security_tier: bool = False,
) -> None:
    user = await lock_user(db, link.funder_user_id)
    if user is None or not user.exchange_bot_id:
        await _comment_not_advertised(installation_id, repo, issue_number, REASON_NO_EXCHANGE)
        await _flush(db)
        return
    # The exchange read is a checked balance, not a reservation of funds.
    available = await _checked_available(user.exchange_bot_id)
    open_total = await sum_advertised_cost(db, user.id)
    amount_ate = int(content["amount_ate"])
    if not ledger_allows(open_total, escrow_cost(amount_ate), available):
        logger.info(
            "not advertising %s#%s; checked balance %s, advertised %s, requested %s",
            repo,
            issue_number,
            available,
            open_total,
            amount_ate,
        )
        await _comment_not_advertised(
            installation_id,
            repo,
            issue_number,
            REASON_INSUFFICIENT,
            balance_notice=True,
        )
        await _flush(db)
        return
    raw_title = issue.get("title")
    if not isinstance(raw_title, str) or not raw_title.strip():
        raw_title = f"GitHub bounty {repo}#{issue_number}"
    description = issue.get("body") if isinstance(issue.get("body"), str) else ""
    bounty = Bounty(
        id=uuid.uuid4(),
        requester_id=user.id,
        title=raw_title.strip()[:500],
        description=description,
        tags=["github", repo],
        reward_amount=amount_ate,
        escrow_id="pending_claim",
        status=BountyStatus.OPEN,
        min_reputation=content["required_ema"],
        difficulty=Difficulty.MEDIUM,
        provenance_tier=ProvenanceTier.TIER1_SELF_DECLARED,
        mode=BountyMode.PRODUCTION,
    )
    db.add(bounty)
    await _flush(db)
    binding = GitHubBountyBinding(
        id=uuid.uuid4(),
        bounty_id=bounty.id,
        repo_full_name=repo,
        issue_number=issue_number,
        state=BindingState.ADVERTISED.value,
    )
    db.add(binding)
    await _flush(db)
    spec = _stage_spec(
        db, binding.id, 1, content, content_hash, security_tier=security_tier
    )
    await _flush(db)
    binding.current_spec_id = spec.id
    db.add(
        FunderLedgerEntry(
            id=uuid.uuid4(),
            funder_user_id=user.id,
            binding_id=binding.id,
            amount_ate=amount_ate,
            status=LedgerStatus.ADVERTISED.value,
        )
    )
    await _refresh_advertised_comment(binding, spec, amount_usd, installation_id)
    await _flush(db)


async def _pinned_lease_amount(db: AsyncSession, binding_id: uuid.UUID) -> int | None:
    """Amount pinned by the active work or submitted lease, if there is one."""
    lease = await get_active_lease(db, binding_id)
    if lease is None:
        return None
    spec = await get_acceptance_spec(db, lease.spec_id)
    if spec is None:
        raise PermanentJobError("active lease acceptance spec is missing")
    return int(spec.amount_ate)


async def _drop_unleased_ledger_to_spec(
    db: AsyncSession, binding: GitHubBountyBinding, spec_amount: int
) -> None:
    """Let the ledger follow the spec once no work or submitted lease remains.

    A decrease applied while a lease was active keeps the higher obligation and
    writes the lower spec immediately. A later unchanged-label sync must be
    able to release that floor, because the spec hash already matches.
    """
    if await get_active_lease(db, binding.id) is not None:
        return
    ledger = await get_ledger_entry(db, binding.id)
    if ledger is None or _enum_value(ledger.status) != LedgerStatus.ADVERTISED.value:
        return
    if int(ledger.amount_ate) <= spec_amount:
        return
    ledger.amount_ate = spec_amount
    bounty = await get_bounty(db, binding.bounty_id)
    if bounty is not None:
        bounty.reward_amount = spec_amount


async def _update_binding_terms(
    db: AsyncSession,
    *,
    binding: GitHubBountyBinding,
    installation_id: int,
    repo: str,
    issue_number: int,
    link,
    content: dict,
    content_hash: str,
    amount_usd: str,
    security_tier: bool = False,
) -> None:
    if binding.current_spec_id is None:
        current = None
    else:
        current = await get_acceptance_spec(db, binding.current_spec_id)
        if current is None:
            raise PermanentJobError("binding acceptance spec is missing")
    if current is not None and current.content_hash == content_hash:
        await _drop_unleased_ledger_to_spec(db, binding, int(current.amount_ate))
        await _refresh_advertised_comment(binding, current, amount_usd, installation_id)
        await _flush(db)
        return
    # The ledger funder keeps the obligation after the repo link's funder changes.
    ledger = await get_ledger_entry(db, binding.id)
    funder_id = link.funder_user_id if ledger is None else ledger.funder_user_id
    user = await lock_user(db, funder_id)
    if user is None:
        await _comment_not_advertised(
            installation_id,
            repo,
            issue_number,
            REASON_NO_EXCHANGE,
            binding=binding,
        )
        await _flush(db)
        return
    previous = 0
    if ledger is not None and _enum_value(ledger.status) == LedgerStatus.ADVERTISED.value:
        previous = int(ledger.amount_ate)
    label_amount = int(content["amount_ate"])
    pinned = await _pinned_lease_amount(db, binding.id)
    amount_ate = label_amount if pinned is None else max(label_amount, pinned)
    delta = amount_ate - previous
    if delta > 0:
        if not user.exchange_bot_id:
            await _comment_not_advertised(
                installation_id,
                repo,
                issue_number,
                REASON_NO_EXCHANGE,
                binding=binding,
            )
            await _flush(db)
            return
        available = await _checked_available(user.exchange_bot_id)
        open_total = await sum_advertised_cost(db, user.id)
        cost_delta = escrow_cost(amount_ate) - escrow_cost(previous)
        if not ledger_allows(open_total, cost_delta, available):
            logger.info(
                "not increasing %s#%s; checked balance %s, advertised %s, delta %s",
                repo,
                issue_number,
                available,
                open_total,
                delta,
            )
            await _comment_not_advertised(
                installation_id,
                repo,
                issue_number,
                REASON_INCREASE_REJECTED,
                binding=binding,
                balance_notice=True,
            )
            await _flush(db)
            return
    version = 1 if current is None else int(current.version) + 1
    spec = _stage_spec(
        db, binding.id, version, content, content_hash, security_tier=security_tier
    )
    await _flush(db)
    binding.current_spec_id = spec.id
    if ledger is None:
        db.add(
            FunderLedgerEntry(
                id=uuid.uuid4(),
                funder_user_id=user.id,
                binding_id=binding.id,
                amount_ate=amount_ate,
                status=LedgerStatus.ADVERTISED.value,
            )
        )
    else:
        ledger.amount_ate = amount_ate
        if _enum_value(ledger.status) != LedgerStatus.SETTLED.value:
            ledger.status = LedgerStatus.ADVERTISED.value
    bounty = await get_bounty(db, binding.bounty_id)
    if bounty is None:
        raise PermanentJobError("bounty binding has no bounty row")
    bounty.reward_amount = amount_ate
    bounty.min_reputation = content["required_ema"]
    await _refresh_advertised_comment(binding, spec, amount_usd, installation_id)
    await _flush(db)


async def _sync_issue_terms(db: AsyncSession, job: GitHubJob) -> None:
    _event, _action, data = _webhook_parts(job)
    issue = data.get("issue")
    if not isinstance(issue, dict):
        raise PermanentJobError("webhook payload is missing issue")
    repo = _repository_full_name(data)
    issue_number = _positive_int(issue.get("number"), "issue.number")
    installation_id = _installation_id(data)
    await _record_repository_visibility(db, repo, data.get("repository"))
    labels = _label_names(issue)
    parsed = parse_bounty_labels(labels)
    binding = await get_binding_by_issue(db, repo, issue_number)
    if binding is not None and _enum_value(binding.state) in _SETTLED_STATES:
        return
    if binding is not None and _state_is(binding, BindingState.CANCELLED):
        return
    if binding is not None and not bounty_label_present(labels):
        await _cancel_binding(db, binding, installation_id, REASON_LABEL_REMOVED)
        return
    if bounty_label_present(labels) and parsed.amount_usd is None:
        await _comment_not_advertised(
            installation_id,
            repo,
            issue_number,
            REASON_INVALID_LABEL_UNCHANGED if binding is not None else REASON_INVALID_LABEL,
            binding=binding,
        )
        await _flush(db)
        return
    if parsed.amount_usd is None:
        return
    link = await get_enabled_funded_repo_link(db, repo)
    if link is None:
        await _comment_not_advertised(
            installation_id,
            repo,
            issue_number,
            REASON_TERMS_UNCHANGED_UNLINKED if binding is not None else REASON_UNLINKED,
            binding=binding,
        )
        await _flush(db)
        return
    _apply_known_visibility(link, data.get("repository"))
    if not await _ensure_visibility_known(link, installation_id, repo):
        if binding is None:
            await _comment_not_advertised(
                installation_id, repo, issue_number, REASON_VISIBILITY_UNKNOWN
            )
            await _flush(db)
            return
        if await get_active_lease(db, binding.id) is not None:
            logger.info(
                "leaving %s#%s in place; repository visibility is unknown and a lease is active",
                repo,
                issue_number,
            )
            return
        await _cancel_binding(db, binding, installation_id, REASON_VISIBILITY_UNKNOWN)
        return
    from app.services import github_alerts

    dispatch = (
        await github_alerts.dispatch_for_issue(db, repo, issue_number)
        if getattr(link, "auto_dispatch_alerts", False) is True
        else None
    )
    content = _build_issue_content(parsed, issue.get("body"), link, dispatch)
    content_hash = spec_content_hash(content)
    amount_usd = format_usd(parsed.amount_usd)
    if binding is None:
        await _create_advertised_bounty(
            db,
            installation_id=installation_id,
            repo=repo,
            issue_number=issue_number,
            issue=issue,
            link=link,
            content=content,
            content_hash=content_hash,
            amount_usd=amount_usd,
            security_tier=parsed.security,
        )
        return
    await _update_binding_terms(
        db,
        binding=binding,
        installation_id=installation_id,
        repo=repo,
        issue_number=issue_number,
        link=link,
        content=content,
        content_hash=content_hash,
        amount_usd=amount_usd,
        security_tier=parsed.security,
    )


async def _link_repos(db: AsyncSession, installation_id: int, entries: object) -> None:
    """Create a disabled link per repo, or move an existing link to this installation."""
    for full_name, repository in _repo_records(entries):
        link = await get_repo_link(db, full_name)
        if link is None:
            db.add(
                GitHubRepoLink(
                    id=uuid.uuid4(),
                    installation_id=installation_id,
                    repo_full_name=full_name,
                    funder_user_id=None,
                    enabled=False,
                    default_policy={},
                    required_checks=[],
                    visibility=visibility_from_repository(repository),
                )
            )
        else:
            if link.installation_id != installation_id:
                link.installation_id = installation_id
            _apply_known_visibility(link, repository)
    await _flush(db)


async def handle_installation(db: AsyncSession, job: GitHubJob) -> None:
    event, action, data = _webhook_parts(job)
    installation_id = _installation_id(data)
    if event == "installation" and action == "created":
        login = _account_login(data)
        existing = await get_installation(db, installation_id)
        if existing is None:
            db.add(
                GitHubInstallation(
                    id=uuid.uuid4(),
                    installation_id=installation_id,
                    account_login=login,
                )
            )
        else:
            existing.account_login = login
            existing.removed_at = None
        await _flush(db)
        # A new installation lists its repositories here; GitHub sends no
        # installation_repositories event for them.
        await _link_repos(db, installation_id, data.get("repositories"))
        return
    if event == "installation" and action == "deleted":
        existing = await get_installation(db, installation_id)
        if existing is not None and existing.removed_at is None:
            existing.removed_at = datetime.now(timezone.utc)
        for link in await list_repo_links_for_installation(db, installation_id):
            link.enabled = False
        await _flush(db)
        return
    if event == "installation_repositories" and action == "added":
        await _link_repos(db, installation_id, data.get("repositories_added"))
        return
    if event == "installation_repositories" and action == "removed":
        for full_name in _repo_names(data.get("repositories_removed")):
            existing = await get_repo_link(db, full_name)
            if existing is not None:
                existing.enabled = False
        await _flush(db)
        return
    logger.info("ignoring installation job event=%s action=%s", event, action)


async def handle_issue_labeled(db: AsyncSession, job: GitHubJob) -> None:
    await _sync_issue_terms(db, job)


async def handle_issue_changed(db: AsyncSession, job: GitHubJob) -> None:
    await _sync_issue_terms(db, job)


async def handle_issue_closed(db: AsyncSession, job: GitHubJob) -> None:
    _event, _action, data = _webhook_parts(job)
    issue = data.get("issue")
    if not isinstance(issue, dict):
        raise PermanentJobError("webhook payload is missing issue")
    repo = _repository_full_name(data)
    issue_number = _positive_int(issue.get("number"), "issue.number")
    binding = await get_binding_by_issue(db, repo, issue_number)
    if binding is None or _enum_value(binding.state) in _PR_IGNORE_STATES:
        return
    await _cancel_binding(db, binding, _installation_id(data), REASON_ISSUE_CLOSED)


async def handle_pull_request_updated(db: AsyncSession, job: GitHubJob) -> None:
    """A pull request is payable only after a lease submission binds it.

    Closing references do not bind. A new head on the bound pull request drops
    the previous evaluation. Settlement, paid, and cancelled bindings are left
    alone.
    """
    _event, action, data = _webhook_parts(job)
    pull_request = data.get("pull_request")
    if not isinstance(pull_request, dict):
        raise PermanentJobError("webhook payload is missing pull_request")
    repo = _repository_full_name(data)
    pr_number = _positive_int(pull_request.get("number"), "pull_request.number")
    binding = await get_binding_by_pull_request(db, repo, pr_number)
    if binding is not None and binding.pr_number == pr_number:
        if _enum_value(binding.state) in _PR_IGNORE_STATES:
            return
        if action != "synchronize":
            return
        head = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
        head_sha = head.get("sha")
        if not isinstance(head_sha, str) or not head_sha:
            raise PermanentJobError("pull request is missing head sha")
        # A redelivery of the evaluated head must not wipe that evaluation.
        if binding.evaluated_head_sha == head_sha:
            return
        _clear_evaluation(binding)
        binding.state = BindingState.ADVERTISED.value
        await _flush(db)
        return
    body = pull_request.get("body")
    issue_number = parse_closing_reference(body if isinstance(body, str) else None, repo)
    if issue_number is None:
        return
    referenced = await get_binding_by_issue(db, repo, issue_number)
    if referenced is None or referenced.pr_number == pr_number:
        return
    logger.info(
        "PR %s references %s#%s but is not the bound pull request; not payable",
        pr_number,
        repo,
        issue_number,
    )


def _stored_assessment(binding) -> dict:
    if isinstance(binding.last_assessment, dict):
        return binding.last_assessment
    return {}


def _stored_outcome(binding) -> str:
    outcome = _stored_assessment(binding).get("outcome")
    return outcome if isinstance(outcome, str) else ""


def _state_for_stored_evaluation(binding) -> str:
    """State implied by the stored mediator result, including a review clear."""
    assessment = _stored_assessment(binding)
    outcome = _stored_outcome(binding)
    cleared_by = binding.review_cleared_by
    if (
        isinstance(cleared_by, str)
        and cleared_by
        and outcome == "NEEDS_REVIEW"
        and not _has_violation(assessment)
    ):
        return BindingState.AWAITING_MERGE.value
    return outcome_to_state(outcome).value


def _review_summary(binding) -> str | None:
    opinion = _stored_assessment(binding).get("reviewer_opinion")
    summary = opinion.get("summary") if isinstance(opinion, dict) else None
    text = summary if isinstance(summary, str) and summary else None
    cleared_by = binding.review_cleared_by
    if isinstance(cleared_by, str) and cleared_by:
        note = f"Review cleared by {cleared_by}."
        if text and note not in text:
            text = f"{text} {note}"
        elif text is None:
            text = note
    return text


async def _comment_stored_evaluation(binding, installation_id: int, spec, head_sha: str) -> None:
    outcome = _stored_outcome(binding)
    if not outcome:
        return
    await _post_evaluation(
        binding,
        installation_id,
        outcome=outcome,
        head_sha=head_sha,
        spec_version=spec.version,
        findings=_finding_list(_stored_assessment(binding).get("findings")),
        reviewer_summary=_review_summary(binding),
    )


async def _restore_passed_checks(
    db: AsyncSession,
    binding: GitHubBountyBinding,
    installation_id: int,
    spec,
    head_sha: str,
) -> None:
    desired = _state_for_stored_evaluation(binding)
    if _enum_value(binding.state) == desired:
        return
    binding.state = desired
    await _comment_stored_evaluation(binding, installation_id, spec, head_sha)
    await _flush(db)


async def _demote_for_checks(
    db: AsyncSession,
    binding: GitHubBountyBinding,
    installation_id: int,
    spec,
    head_sha: str,
    result: ChecksResult,
) -> None:
    """Required checks no longer pass. Advertised is the existing not-passing state."""
    binding.state = BindingState.ADVERTISED.value
    findings = _finding_list(_stored_assessment(binding).get("findings"))
    if result.state == "failed":
        await _post_evaluation(
            binding,
            installation_id,
            outcome="REJECTED_CHECKS",
            head_sha=head_sha,
            spec_version=spec.version,
            findings=findings,
            failed_checks=list(result.failed),
        )
    elif result.state == "unconfigured":
        await _post_evaluation(
            binding,
            installation_id,
            outcome="UNCONFIGURED",
            head_sha=head_sha,
            spec_version=spec.version,
            findings=[],
        )
    else:
        await _post_evaluation(
            binding,
            installation_id,
            outcome="NOT_ACCEPTED",
            head_sha=head_sha,
            spec_version=spec.version,
            findings=findings,
            reviewer_summary=REASON_CHECKS_NOT_PASSING,
        )
    await _flush(db)


async def handle_checks_completed(db: AsyncSession, job: GitHubJob) -> None:
    _event, _action, data = _webhook_parts(job)
    suite = data.get("check_suite")
    if not isinstance(suite, dict):
        raise PermanentJobError("webhook payload is missing check_suite")
    head_sha = suite.get("head_sha")
    if not isinstance(head_sha, str) or not head_sha:
        raise PermanentJobError("check suite is missing head_sha")
    repo = _repository_full_name(data)
    installation_id = _installation_id(data)
    for pr_number in _pull_request_numbers(suite):
        binding = await get_binding_by_pull_request(db, repo, pr_number)
        if binding is None or _enum_value(binding.state) in _PR_IGNORE_STATES:
            continue
        lease = await get_submitted_lease(db, binding.id)
        if lease is None or lease.pr_number != pr_number:
            continue
        already_evaluated = (
            binding.evaluated_head_sha == head_sha
            and binding.evaluated_spec_id == lease.spec_id
        )
        spec = await get_acceptance_spec(db, lease.spec_id)
        if spec is None:
            logger.info("acceptance spec %s is missing", lease.spec_id)
            continue
        pull_request = await _github_call(
            github_app.get_pull_request(installation_id, repo, pr_number)
        )
        if not isinstance(pull_request, dict):
            raise PermanentJobError("pull request response is not an object")
        head = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
        if head.get("sha") != head_sha or pull_request.get("state") != "open":
            continue
        runs = await _github_call(github_app.list_check_runs(installation_id, repo, head_sha))
        if not isinstance(runs, list):
            raise PermanentJobError("check run response is not a list")
        result = evaluate_required_checks(spec.required_checks or [], runs, head_sha)
        if already_evaluated:
            # The mediator result is for this head and spec. Re-running checks
            # must not call it again; a failing rerun only drops the merge gate.
            if result.state == "passed":
                await _restore_passed_checks(db, binding, installation_id, spec, head_sha)
            elif _state_is(binding, BindingState.AWAITING_MERGE, BindingState.NEEDS_REVIEW):
                await _demote_for_checks(db, binding, installation_id, spec, head_sha, result)
            continue
        if result.state == "pending":
            continue
        if result.state == "unconfigured":
            binding.state = BindingState.ADVERTISED.value
            binding.last_assessment = {
                "outcome": "UNCONFIGURED",
                "head_sha": head_sha,
                "spec_id": str(spec.id),
            }
            await _post_evaluation(
                binding,
                installation_id,
                outcome="UNCONFIGURED",
                head_sha=head_sha,
                spec_version=spec.version,
                findings=[],
            )
            await _flush(db)
            continue
        if result.state == "failed":
            binding.state = BindingState.ADVERTISED.value
            binding.last_assessment = {
                "outcome": "REJECTED_CHECKS",
                "failed_checks": list(result.failed),
                "head_sha": head_sha,
            }
            await _post_evaluation(
                binding,
                installation_id,
                outcome="REJECTED_CHECKS",
                head_sha=head_sha,
                spec_version=spec.version,
                findings=[],
                failed_checks=list(result.failed),
            )
            await _flush(db)
            continue
        base = pull_request.get("base") if isinstance(pull_request.get("base"), dict) else {}
        base_sha = base.get("sha")
        if not isinstance(base_sha, str) or not base_sha:
            raise PermanentJobError("pull request is missing base sha")
        diff = await _github_call(
            github_app.compare_diff(installation_id, repo, base_sha, head_sha)
        )
        if not isinstance(diff, str):
            raise PermanentJobError("compare diff response is not text")
        payload = {
            "spec_hash": spec.content_hash,
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "git_diff": diff,
            "policy": copy.deepcopy(spec.policy or {}),
            "bounty_id": str(binding.bounty_id),
            "solver_agent_id": lease.agent_id,
            "enable_reviewer": True,
        }
        try:
            assessment = await mediator.verify_patch(payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 413:
                _reraise_external(exc)
                raise
            assessment = {"outcome": "REJECTED_TOO_LARGE", "findings": []}
        except Exception as exc:
            _reraise_external(exc)
            raise
        if not isinstance(assessment, dict):
            raise PermanentJobError("mediator verify_patch response is not an object")
        stored = dict(assessment)
        stored["spec_id"] = str(spec.id)
        binding.last_assessment = stored
        binding.evaluated_head_sha = head_sha
        binding.evaluated_spec_id = lease.spec_id
        # A maintainer clearance applied to the previous evaluation only.
        binding.review_cleared_by = None
        outcome = str(assessment.get("outcome") or "")
        if outcome not in _KNOWN_OUTCOMES:
            logger.warning("unknown mediator outcome %s; leaving the bounty advertised", outcome)
        binding.state = outcome_to_state(outcome).value
        opinion = assessment.get("reviewer_opinion")
        summary = opinion.get("summary") if isinstance(opinion, dict) else None
        await _post_evaluation(
            binding,
            installation_id,
            outcome=outcome,
            head_sha=head_sha,
            spec_version=spec.version,
            findings=_finding_list(assessment.get("findings")),
            reviewer_summary=summary if isinstance(summary, str) else None,
        )
        await _flush(db)
        if outcome == "ACCEPTABLE" and settings.GITHUB_AUTO_MERGE_ACCEPTABLE:
            from app.services import github_automerge

            link = await get_enabled_funded_repo_link(db, repo)
            if link is not None:
                await github_automerge.maybe_auto_merge(
                    db,
                    binding=binding,
                    lease=lease,
                    spec=spec,
                    link=link,
                    installation_id=installation_id,
                    repo=repo,
                    pr_number=pr_number,
                    pull_request=pull_request,
                    head_sha=head_sha,
                    assessment=assessment,
                )


async def handle_review_clear_comment(db: AsyncSession, job: GitHubJob) -> None:
    _event, _action, data = _webhook_parts(job)
    issue = data.get("issue")
    if not isinstance(issue, dict) or "pull_request" not in issue:
        return
    comment = data.get("comment")
    if not isinstance(comment, dict):
        raise PermanentJobError("webhook payload is missing comment")
    user = comment.get("user")
    if not isinstance(user, dict) or not isinstance(user.get("login"), str):
        raise PermanentJobError("webhook payload is missing comment user")
    login = user["login"]
    repo = _repository_full_name(data)
    installation_id = _installation_id(data)
    pr_number = _positive_int(issue.get("number"), "issue.number")
    try:
        permission = await github_app.get_collaborator_permission(installation_id, repo, login)
    except github_app.GitHubAPIError as exc:
        if exc.status_code == 404:
            logger.info("ignoring review clear from non-collaborator %s", login)
            return
        _reraise_external(exc)
        raise
    except Exception as exc:
        _reraise_external(exc)
        raise
    if str(permission).lower() not in _WRITE_PERMISSIONS:
        logger.info("ignoring review clear from %s with permission %s", login, permission)
        return
    binding = await get_binding_by_pull_request(db, repo, pr_number)
    if binding is None or not _state_is(binding, BindingState.NEEDS_REVIEW):
        return
    lease = await get_submitted_lease(db, binding.id)
    if lease is None or lease.pr_number != pr_number:
        logger.info("ignoring review clear; no submitted lease for PR %s", pr_number)
        return
    pull_request = await _github_call(github_app.get_pull_request(installation_id, repo, pr_number))
    if not isinstance(pull_request, dict):
        raise PermanentJobError("pull request response is not an object")
    author_obj = pull_request.get("user") if isinstance(pull_request.get("user"), dict) else {}
    author = author_obj.get("login")
    if isinstance(author, str) and author.casefold() == login.casefold():
        logger.info("ignoring review clear by pull request author %s", login)
        return
    head = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
    head_sha = head.get("sha")
    if not isinstance(head_sha, str) or head_sha != binding.evaluated_head_sha:
        logger.info("ignoring review clear; head does not match the evaluated commit")
        return
    assessment = binding.last_assessment if isinstance(binding.last_assessment, dict) else {}
    if _has_violation(assessment):
        logger.info("ignoring review clear; violation findings are present")
        return
    binding.state = BindingState.AWAITING_MERGE.value
    binding.review_cleared_by = login
    spec = await get_acceptance_spec(db, lease.spec_id)
    opinion = assessment.get("reviewer_opinion")
    prior = opinion.get("summary") if isinstance(opinion, dict) else None
    summary = f"Review cleared by {login}."
    if isinstance(prior, str) and prior:
        summary = f"{prior} Review cleared by {login}."
    await _post_evaluation(
        binding,
        installation_id,
        outcome=str(assessment.get("outcome") or "NEEDS_REVIEW"),
        head_sha=head_sha,
        spec_version=spec.version if spec is not None else 0,
        findings=_finding_list(assessment.get("findings")),
        reviewer_summary=summary,
    )
    await _flush(db)


def _acceptance_comment_reason() -> str | None:
    if not settings.GITHUB_SETTLEMENT_ENABLED:
        return REASON_SETTLEMENT_DISABLED
    return None


async def handle_pull_request_closed(db: AsyncSession, job: GitHubJob) -> None:
    _event, _action, data = _webhook_parts(job)
    pull_request = data.get("pull_request")
    if not isinstance(pull_request, dict):
        raise PermanentJobError("webhook payload is missing pull_request")
    repo = _repository_full_name(data)
    installation_id = _installation_id(data)
    pr_number = _positive_int(pull_request.get("number"), "pull_request.number")
    binding = await get_binding_by_pull_request(db, repo, pr_number)
    if binding is None or _enum_value(binding.state) in _PR_IGNORE_STATES:
        return
    if not pull_request.get("merged"):
        lease = await get_submitted_lease(db, binding.id)
        if lease is not None and lease.pr_number == pr_number:
            _release_lease(db, lease, "pr_closed_unmerged")
        binding.state = BindingState.ADVERTISED.value
        _unbind_pull_request(binding)
        await _flush(db)
        return
    # The audited diff is the pull request head that was merged, not merge_commit_sha.
    head = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
    merged_sha = head.get("sha")
    if not isinstance(merged_sha, str) or not merged_sha:
        raise PermanentJobError("merged pull request is missing head sha")
    lease = await get_submitted_lease(db, binding.id)
    accepted, reason = merge_acceptance_decision(binding, merged_sha, lease)
    if not accepted:
        if reason == REASON_NO_LEASE_SUBMISSION:
            body = github_comments.render_not_payable(
                bounty_id=binding.bounty_id, reason=reason
            )
            marker = github_comments.bounty_marker(binding.bounty_id)
            binding.comment_id = await _sticky(
                installation_id,
                repo,
                binding.issue_number,
                marker,
                body,
                binding.comment_id,
            )
            await _flush(db)
            return
        spec = None
        if binding.current_spec_id is not None:
            spec = await get_acceptance_spec(db, binding.current_spec_id)
        assessment = binding.last_assessment if isinstance(binding.last_assessment, dict) else {}
        await _post_evaluation(
            binding,
            installation_id,
            outcome="NOT_ACCEPTED",
            head_sha=merged_sha,
            spec_version=spec.version if spec is not None else 0,
            findings=_finding_list(assessment.get("findings")),
            reviewer_summary=reason,
        )
        await _flush(db)
        return
    spec = await get_acceptance_spec(db, lease.spec_id)
    if spec is None:
        raise PermanentJobError("leased acceptance spec is missing")
    runs = await _github_call(github_app.list_check_runs(installation_id, repo, merged_sha))
    if not isinstance(runs, list):
        raise PermanentJobError("check run response is not a list")
    checks = evaluate_required_checks(spec.required_checks or [], runs, merged_sha)
    # Nothing re-runs this decision for a closed PR, so wait for running or missing
    # checks while attempts remain; the final attempt reports them as not passing.
    if checks.state == "pending" and int(job.attempts or 0) < settings.GITHUB_JOB_MAX_ATTEMPTS:
        raise RetryableJobError("required checks are not complete on the merged head")
    if checks.state != "passed":
        assessment = binding.last_assessment if isinstance(binding.last_assessment, dict) else {}
        await _post_evaluation(
            binding,
            installation_id,
            outcome="NOT_ACCEPTED",
            head_sha=merged_sha,
            spec_version=spec.version,
            findings=_finding_list(assessment.get("findings")),
            failed_checks=list(checks.failed) or None,
            reviewer_summary=REASON_CHECKS_NOT_PASSING,
        )
        await _flush(db)
        return
    existing = await get_settlement_for_binding(db, binding.id)
    if existing is None:
        settlement = GitHubSettlement(
            id=uuid.uuid4(),
            binding_id=binding.id,
            spec_id=lease.spec_id,
            solver_agent_id=lease.agent_id,
            head_sha=merged_sha,
            state=SettlementState.PENDING.value,
            idempotency_prefix=f"gh:{binding.id}:{lease.spec_id}",
        )
        db.add(settlement)
        await _flush(db)
        db.add(
            GitHubJob(
                id=uuid.uuid4(),
                kind=JobKind.ADVANCE_SETTLEMENT.value,
                payload={"settlement_id": str(settlement.id)},
                status=JobStatus.PENDING.value,
            )
        )
    lease.status = LeaseStatus.SETTLED.value
    binding.state = BindingState.SETTLEMENT_PENDING.value
    body = github_comments.render_accepted_payment_pending(
        bounty_id=binding.bounty_id,
        head_sha=merged_sha,
        reason=_acceptance_comment_reason(),
    )
    marker = github_comments.bounty_marker(binding.bounty_id)
    binding.comment_id = await _sticky(
        installation_id,
        repo,
        binding.issue_number,
        marker,
        body,
        binding.comment_id,
    )
    await _flush(db)


async def handle_repository_event(db: AsyncSession, job: GitHubJob) -> None:
    """Store visibility after a repository is privatized or publicized.

    Bindings stay as they are. Going private hides bounties through the visibility
    column alone. A payload that does not name public, private, or internal is left
    unchanged. ``publicized`` ends as public. ``privatized`` stores the payload
    visibility when that value is one of those three, which is private for a
    normal privatized event.
    """
    _event, action, data = _webhook_parts(job)
    repo = _repository_full_name(data)
    reported = visibility_from_repository(data.get("repository"))
    if reported not in _REPOSITORY_VISIBILITIES:
        return
    if action == "publicized":
        visibility = "public"
    else:
        visibility = reported
    link = await get_repo_link(db, repo)
    if link is None:
        return
    link.visibility = visibility
    await _flush(db)


async def handle_advance_settlement(db: AsyncSession, job: GitHubJob) -> None:
    payload = job.payload
    if not isinstance(payload, dict):
        raise PermanentJobError("job payload must be an object")
    raw_id = payload.get("settlement_id")
    if not isinstance(raw_id, str):
        raise PermanentJobError("ADVANCE_SETTLEMENT payload is missing settlement_id")
    try:
        settlement_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise PermanentJobError("settlement_id is not a UUID") from exc
    await github_settlement.advance_settlement(db, settlement_id)


_HANDLERS = {
    JobKind.INSTALLATION.value: handle_installation,
    JobKind.ISSUE_LABELED.value: handle_issue_labeled,
    JobKind.ISSUE_CHANGED.value: handle_issue_changed,
    JobKind.ISSUE_CLOSED.value: handle_issue_closed,
    JobKind.PR_UPDATED.value: handle_pull_request_updated,
    JobKind.PR_CLOSED.value: handle_pull_request_closed,
    JobKind.CHECKS_COMPLETED.value: handle_checks_completed,
    JobKind.REVIEW_CLEAR_COMMENT.value: handle_review_clear_comment,
    JobKind.ADVANCE_SETTLEMENT.value: handle_advance_settlement,
    JobKind.REPOSITORY.value: handle_repository_event,
}


async def handle_security_alert(db: AsyncSession, job: GitHubJob) -> None:
    from app.services import github_alerts

    await github_alerts.handle_security_alert(db, job)


async def handle_refund_hold(db: AsyncSession, job: GitHubJob) -> None:
    from app.services import github_leases

    await github_leases.handle_refund_hold(db, job)


_HANDLERS[JobKind.SECURITY_ALERT.value] = handle_security_alert
_HANDLERS[JobKind.REFUND_HOLD.value] = handle_refund_hold


async def handle_job(db: AsyncSession, job: GitHubJob) -> None:
    handler = _HANDLERS.get(_enum_value(job.kind))
    if handler is None:
        raise PermanentJobError(f"unknown GitHub job kind: {job.kind}")
    await handler(db, job)
