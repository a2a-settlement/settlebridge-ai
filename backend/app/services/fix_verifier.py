"""Public fix-verifier orchestration.

The private package is reached only through ``verifier_client``. This module
checks the feature flag, the repository opt-in, alert linkage, the result
cache, PyPI scope, and quota, then stores the verdict. ``solver_kind`` is
stored as the caller sent it and is never rewritten from the verifier result.

``request_verification_receipt`` is the mediator hook. It does not fetch a
receipt; a later change will.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import object_session

from app.config import settings
from app.middleware.auth import is_platform_admin
from app.models.fix_verifier import (
    FixVerdict,
    FixVerification,
    FixVerificationState,
    SolverKind,
)
from app.models.github import GitHubInstallation, GitHubRepoLink
from app.services import verifier_quota
from app.services.disclosure import verifier_may_run
from app.services.verifier_client import verify
from app.services.verifier_quota import (
    charge_verification_spend,
    release_verification_slot,
    reserve_verification_slot,
)

logger = logging.getLogger(__name__)

EMBARGO_PRIVATE = "private"
EMBARGO_NONE = "none"

_VERDICTS = frozenset(item.value for item in FixVerdict)
_SOLVER_KINDS = frozenset(item.value for item in SolverKind)
_UNSUPPORTED_RESULT = {
    "verdict": "inconclusive",
    "reason": "unsupported_project",
    "vex_status": "under_investigation",
    "detail": "launch scope is pure-Python PyPI only",
}
_FAILURE_RESULT = {
    "verdict": "inconclusive",
    "reason": "infra_error",
    "vex_status": "under_investigation",
    "detail": "verifier client raised",
}


class FeatureDisabled(Exception):
    """``FIX_VERIFIER_ENABLED`` is false. Routes answer 404."""


class NotOptedIn(Exception):
    """The repository link is missing, disabled, or has not opted in."""


class InvalidVerificationRequest(Exception):
    """The request cannot be stored. ``code`` is the HTTP ``detail``."""

    def __init__(self, code: str = "invalid_request"):
        self.code = code
        super().__init__(code)


class AdvisoryRequired(InvalidVerificationRequest):
    """The pull request is not linked to an alert or an advisory."""

    def __init__(self) -> None:
        super().__init__("alert_or_advisory_required")


def _as_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _same(left: object, right: object) -> bool:
    parsed_left = _as_uuid(left)
    parsed_right = _as_uuid(right)
    return parsed_left is not None and parsed_left == parsed_right


def _user_id(user: object) -> uuid.UUID | None:
    return _as_uuid(getattr(user, "id", None))


def _state(row: FixVerification) -> str:
    value = row.state
    if isinstance(value, FixVerificationState):
        return value.value
    return str(value)


def _solver_kind(value: object) -> str:
    """Pass ``house`` through. Never promote an unrecognised value to third_party."""
    if not isinstance(value, str):
        return SolverKind.UNKNOWN.value
    kind = value.strip().casefold().replace("-", "_")
    if kind in _SOLVER_KINDS:
        return kind
    return SolverKind.UNKNOWN.value


def _sha(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidVerificationRequest("invalid_request")
    text = value.strip().lower()
    if not text or len(text) > 40:
        raise InvalidVerificationRequest("invalid_request")
    return text


def _alert_ref(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidVerificationRequest("invalid_request")
    text = value.strip()
    if not text:
        return None
    if len(text) > 255:
        raise InvalidVerificationRequest("invalid_request")
    return text


def _principal(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidVerificationRequest("invalid_request")
    text = value.strip()
    if not text:
        return None
    if len(text) > 255:
        raise InvalidVerificationRequest("invalid_request")
    return text


def _advisory(value: object) -> dict:
    if not isinstance(value, dict):
        raise InvalidVerificationRequest("invalid_request")
    return value


def _vuln_id(advisory: dict, alert_ref: str | None) -> str:
    raw = advisory.get("vuln_id")
    if isinstance(raw, str) and raw.strip():
        vuln = raw.strip()
        if len(vuln) > 64:
            raise InvalidVerificationRequest("invalid_request")
        return vuln
    if alert_ref:
        if len(alert_ref) <= 64:
            return alert_ref
        return hashlib.sha256(alert_ref.encode("utf-8")).hexdigest()
    raise AdvisoryRequired()


def _package_name(package: object) -> str:
    if isinstance(package, dict):
        package = package.get("name")
    if not isinstance(package, str):
        return ""
    return package.strip()


def supported_project(advisory: object) -> bool:
    """True only for a named package on PyPI.

    Ecosystem comparison matches ``verifier_client`` (strip, case-insensitive).
    A missing package name is out of scope so we do not spend before we know
    which project would be checked. This process does not call PyPI.
    """
    if not isinstance(advisory, dict):
        return False
    ecosystem = advisory.get("ecosystem")
    if not isinstance(ecosystem, str) or ecosystem.strip().casefold() != "pypi":
        return False
    return bool(_package_name(advisory.get("package")))


def _principal_sha256(principal: str | None) -> str | None:
    if principal is None:
        return None
    if len(principal) == 64 and all(char in "0123456789abcdefABCDEF" for char in principal):
        return principal.lower()
    return hashlib.sha256(principal.encode("utf-8")).hexdigest()


def _budget_cents() -> int:
    raw = settings.FIX_VERIFIER_MAX_COST_CENTS_PER_VERIFICATION
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def _charged_cost(result: dict) -> int:
    """Actual ``cost_cents``, clamped to the budget reserved against the daily cap."""
    budget = _budget_cents()
    raw = result.get("cost_cents", 0)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    if raw > budget:
        return budget
    return raw


def _client_request(
    *,
    repo: str,
    pr: int,
    base_sha: str,
    fix_sha: str,
    advisory: dict,
    solver_kind: str,
    principal: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "repo": repo,
        "pr": pr,
        "base_commit": base_sha,
        "fix_commit": fix_sha,
        "advisory": advisory,
        "solver_kind": solver_kind,
        "budget_cents": _budget_cents(),
    }
    digest = _principal_sha256(principal)
    if digest is not None:
        body["solver_principal_sha256"] = digest
    return body


def _inputs(
    *,
    advisory: dict,
    alert_ref: str | None,
    pr: int,
    base_sha: str,
    fix_sha: str,
    solver_kind: str,
    principal: str | None,
) -> dict:
    return {
        "advisory": advisory,
        "alert_ref": alert_ref,
        "pr": pr,
        "base_sha": base_sha,
        "fix_sha": fix_sha,
        "solver_kind": solver_kind,
        "solver_principal_id": principal,
    }


def _reason(result: dict, verdict: str) -> str | None:
    if verdict != FixVerdict.INCONCLUSIVE.value:
        return None
    raw = result.get("reason")
    if not isinstance(raw, str) or not raw.strip():
        return "infra_error"
    return raw.strip()[:48]


def _complete(
    row: FixVerification,
    *,
    state: str,
    verdict: str,
    reason: str | None,
    result: dict,
    cost: int,
) -> None:
    row.state = state
    row.verdict = verdict
    row.inconclusive_reason = reason
    row.result = result
    row.cost_cents = cost
    row.embargo_state = (
        EMBARGO_PRIVATE if verdict == FixVerdict.INCOMPLETE_FIX_CONFIRMED.value else EMBARGO_NONE
    )
    row.completed_at = verifier_quota._utcnow()


def _apply_outcome(row: FixVerification, outcome: dict, *, cost: int) -> None:
    verdict = outcome.get("verdict")
    if verdict not in _VERDICTS:
        verdict = FixVerdict.INCONCLUSIVE.value
    # solver_kind stays what the caller passed. Ignore outcome["solver_kind"].
    _complete(
        row,
        state=FixVerificationState.DONE.value,
        verdict=str(verdict),
        reason=_reason(outcome, str(verdict)),
        result=outcome,
        cost=cost,
    )


def _apply_unsupported(row: FixVerification) -> None:
    _complete(
        row,
        state=FixVerificationState.DONE.value,
        verdict=FixVerdict.INCONCLUSIVE.value,
        reason="unsupported_project",
        result=dict(_UNSUPPORTED_RESULT),
        cost=0,
    )


def _apply_failure(row: FixVerification) -> None:
    _complete(
        row,
        state=FixVerificationState.FAILED.value,
        verdict=FixVerdict.INCONCLUSIVE.value,
        reason="infra_error",
        result=dict(_FAILURE_RESULT),
        cost=0,
    )


def _is_unsupported_outcome(result: dict) -> bool:
    return (
        result.get("verdict") == FixVerdict.INCONCLUSIVE.value
        and result.get("reason") == "unsupported_project"
    )


def _is_operator(user_id: object) -> bool:
    return is_platform_admin(user_id)


def _is_owner(user_id: object, installation: GitHubInstallation | None) -> bool:
    if installation is None:
        return False
    return _same(user_id, installation.owner_user_id)


def _is_funder(user_id: object, link: GitHubRepoLink | None) -> bool:
    if link is None:
        return False
    return _same(user_id, link.funder_user_id)


def _verdict_value(verification: FixVerification) -> str | None:
    verdict = verification.verdict
    if isinstance(verdict, FixVerdict):
        return verdict.value
    if isinstance(verdict, str):
        return verdict
    return None


def _is_embargoed(verification: FixVerification) -> bool:
    return (
        _verdict_value(verification) == FixVerdict.INCOMPLETE_FIX_CONFIRMED.value
        and verification.embargo_state == EMBARGO_PRIVATE
    )


def viewer_can_read(
    user_id: object,
    verification: FixVerification | None,
    link: GitHubRepoLink | None,
    installation: GitHubInstallation | None,
) -> bool:
    """Installation owner, repo-link funder, or platform operator."""
    if verification is None:
        return False
    return _is_operator(user_id) or _is_owner(user_id, installation) or _is_funder(user_id, link)


def viewer_can_see_details(
    user_id: object,
    verification: FixVerification,
    installation: GitHubInstallation | None,
) -> bool:
    """Embargoed incomplete-fix details stay with the installation owner and operators.

    A funder can still read the verification row. Callers redact ``result`` down
    to the verdict. Any ``embargo_state`` other than ``private`` reveals details.
    """
    if not _is_embargoed(verification):
        return True
    return _is_operator(user_id) or _is_owner(user_id, installation)


def viewer_can_replay(
    user_id: object,
    link: GitHubRepoLink | None,
    installation: GitHubInstallation | None,
) -> bool:
    """Replay is the installation owner or an operator. The funder is not enough."""
    del link
    return _is_operator(user_id) or _is_owner(user_id, installation)


def _detach(db, obj: object) -> None:
    if object_session(obj) is not None:
        db.expunge(obj)


async def _repo_link(db, repo_full_name: object) -> GitHubRepoLink | None:
    if not isinstance(repo_full_name, str):
        return None
    key = repo_full_name.strip().lower()
    if not key:
        return None
    stmt = select(GitHubRepoLink).where(func.lower(GitHubRepoLink.repo_full_name) == key).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find(db, repo_full_name: str, fix_sha: str, vuln_id: str) -> FixVerification | None:
    stmt = select(FixVerification).where(
        FixVerification.repo_full_name == repo_full_name,
        FixVerification.fix_sha == fix_sha,
        FixVerification.vuln_id == vuln_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _installation(db, installation_id: int) -> GitHubInstallation | None:
    stmt = select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def access_context(db, verification: FixVerification):
    """Load the repo link and GitHub installation used for visibility checks."""
    link = None
    if verification.repo_link_id is not None:
        stmt = select(GitHubRepoLink).where(GitHubRepoLink.id == verification.repo_link_id)
        link = (await db.execute(stmt)).scalar_one_or_none()
    if link is None and verification.repo_full_name:
        link = await _repo_link(db, verification.repo_full_name)
    installation_id = None
    if link is not None:
        installation_id = link.installation_id
    elif verification.installation_id is not None:
        installation_id = verification.installation_id
    installation = None
    if installation_id is not None:
        installation = await _installation(db, int(installation_id))
    return link, installation


async def _insert(db, row: FixVerification) -> FixVerification:
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        _detach(db, row)
        found = await _find(db, row.repo_full_name, row.fix_sha, row.vuln_id)
        if found is None:
            raise
        return found
    return row


def _draft(
    *,
    link: GitHubRepoLink,
    vuln_id: str,
    alert_ref: str | None,
    pr: int,
    base_sha: str,
    fix_sha: str,
    advisory: dict,
    solver_kind: str,
    principal: str | None,
    user: object,
) -> FixVerification:
    return FixVerification(
        id=uuid.uuid4(),
        repo_link_id=link.id,
        installation_id=int(link.installation_id),
        repo_full_name=link.repo_full_name,
        vuln_id=vuln_id,
        alert_ref=alert_ref,
        pr_number=pr,
        base_sha=base_sha,
        fix_sha=fix_sha,
        state=FixVerificationState.QUEUED.value,
        verdict=None,
        inconclusive_reason=None,
        inputs=_inputs(
            advisory=advisory,
            alert_ref=alert_ref,
            pr=pr,
            base_sha=base_sha,
            fix_sha=fix_sha,
            solver_kind=solver_kind,
            principal=principal,
        ),
        result={},
        cost_cents=0,
        receipt_id=None,
        embargo_state=EMBARGO_NONE,
        solver_kind=solver_kind,
        solver_principal_id=principal,
        created_by_user_id=_user_id(user),
        created_at=verifier_quota._utcnow(),
        completed_at=None,
    )


def _fill(
    row: FixVerification,
    *,
    link: GitHubRepoLink,
    alert_ref: str | None,
    pr: int,
    base_sha: str,
    fix_sha: str,
    advisory: dict,
    solver_kind: str,
    principal: str | None,
    user: object,
) -> None:
    row.repo_link_id = link.id
    row.installation_id = int(link.installation_id)
    row.base_sha = base_sha
    row.pr_number = pr
    row.alert_ref = alert_ref
    row.solver_kind = solver_kind
    row.solver_principal_id = principal
    row.inputs = _inputs(
        advisory=advisory,
        alert_ref=alert_ref,
        pr=pr,
        base_sha=base_sha,
        fix_sha=fix_sha,
        solver_kind=solver_kind,
        principal=principal,
    )
    if row.created_by_user_id is None:
        row.created_by_user_id = _user_id(user)


async def _finish(db, row: FixVerification) -> FixVerification:
    await db.flush()
    if _state(row) == FixVerificationState.DONE.value:
        await request_verification_receipt(db, row)
    return row


async def request_verification_receipt(db, verification: FixVerification) -> str | None:
    """Mediator receipt hook. A later change will issue the receipt.

    This body does not call the mediator, does not write, and does not invent
    an id. It returns the receipt id already stored on the row. Safe to call
    more than once. ``db`` is unused until that later change persists the id.
    """
    del db
    receipt_id = getattr(verification, "receipt_id", None)
    if not isinstance(receipt_id, str):
        return None
    receipt_id = receipt_id.strip()
    if not receipt_id:
        return None
    return receipt_id


async def request_verification(
    db,
    *,
    repo_full_name: str,
    pr: int,
    base_sha: str,
    fix_sha: str,
    advisory: dict,
    alert_ref: str | None,
    solver_kind: str,
    solver_principal_id: str | None,
    user: object,
    replay: bool = False,
) -> FixVerification:
    """Run one verification, or return the cached done row for the same fix.

    ``replay`` ignores a done row and spends quota again. An unsupported
    project is stored as inconclusive with cost 0 and does not take a slot.
    """
    if not settings.FIX_VERIFIER_ENABLED:
        raise FeatureDisabled()
    advisory = _advisory(advisory)
    link = await _repo_link(db, repo_full_name)
    if link is None or not verifier_may_run(link):
        raise NotOptedIn()
    clean_alert = _alert_ref(alert_ref)
    vuln_id = _vuln_id(advisory, clean_alert)
    clean_base = _sha(base_sha)
    clean_fix = _sha(fix_sha)
    kind = _solver_kind(solver_kind)
    principal = _principal(solver_principal_id)
    if isinstance(pr, bool) or not isinstance(pr, int):
        raise InvalidVerificationRequest("invalid_request")

    existing = await _find(db, link.repo_full_name, clean_fix, vuln_id)
    if existing is not None and _state(existing) == FixVerificationState.DONE.value and not replay:
        return existing

    if not supported_project(advisory):
        row = existing
        if row is None:
            row = _draft(
                link=link,
                vuln_id=vuln_id,
                alert_ref=clean_alert,
                pr=pr,
                base_sha=clean_base,
                fix_sha=clean_fix,
                advisory=advisory,
                solver_kind=kind,
                principal=principal,
                user=user,
            )
            row = await _insert(db, row)
            if _state(row) == FixVerificationState.RUNNING.value:
                return row
            if _state(row) == FixVerificationState.DONE.value and _verdict_value(row) not in (
                None,
                FixVerdict.INCONCLUSIVE.value,
            ):
                return row
        _fill(
            row,
            link=link,
            alert_ref=clean_alert,
            pr=pr,
            base_sha=clean_base,
            fix_sha=clean_fix,
            advisory=advisory,
            solver_kind=kind,
            principal=principal,
            user=user,
        )
        _apply_unsupported(row)
        return await _finish(db, row)

    reservation = await reserve_verification_slot(
        db, installation_id=int(link.installation_id), repo_full_name=link.repo_full_name
    )
    slot_open = True

    async def give_back() -> None:
        nonlocal slot_open
        if slot_open:
            slot_open = False
            await release_verification_slot(db, reservation)

    row = existing
    if row is None:
        row = _draft(
            link=link,
            vuln_id=vuln_id,
            alert_ref=clean_alert,
            pr=pr,
            base_sha=clean_base,
            fix_sha=clean_fix,
            advisory=advisory,
            solver_kind=kind,
            principal=principal,
            user=user,
        )
        stored = await _insert(db, row)
        if stored is not row:
            await give_back()
            if _state(stored) == FixVerificationState.DONE.value and not replay:
                return stored
            if _state(stored) == FixVerificationState.RUNNING.value:
                return stored
            row = stored
            reservation = await reserve_verification_slot(
                db,
                installation_id=int(link.installation_id),
                repo_full_name=link.repo_full_name,
            )
            slot_open = True

    _fill(
        row,
        link=link,
        alert_ref=clean_alert,
        pr=pr,
        base_sha=clean_base,
        fix_sha=clean_fix,
        advisory=advisory,
        solver_kind=kind,
        principal=principal,
        user=user,
    )
    row.receipt_id = None
    row.state = FixVerificationState.RUNNING.value
    await db.flush()

    try:
        outcome = verify(
            _client_request(
                repo=link.repo_full_name,
                pr=pr,
                base_sha=clean_base,
                fix_sha=clean_fix,
                advisory=advisory,
                solver_kind=kind,
                principal=principal,
            )
        )
    except Exception as exc:
        logger.error("fix verifier client raised (%s)", type(exc).__name__)
        _apply_failure(row)
        return await _finish(db, row)
    if not isinstance(outcome, dict):
        _apply_failure(row)
        return await _finish(db, row)
    if _is_unsupported_outcome(outcome):
        await give_back()
        _apply_outcome(row, outcome, cost=0)
        return await _finish(db, row)

    cost = _charged_cost(outcome)
    _apply_outcome(row, outcome, cost=cost)
    await charge_verification_spend(db, reservation, cost)
    slot_open = False
    return await _finish(db, row)


async def replay_verification(
    db, verification: FixVerification, *, user: object
) -> FixVerification:
    """Re-run a stored verification, ignoring the done cache and spending quota."""
    inputs = verification.inputs if isinstance(verification.inputs, dict) else {}
    advisory = inputs.get("advisory")
    if not isinstance(advisory, dict):
        advisory = {}
    if not (isinstance(advisory.get("vuln_id"), str) and advisory.get("vuln_id").strip()):
        advisory = {**advisory, "vuln_id": verification.vuln_id}
    principal = inputs.get("solver_principal_id", verification.solver_principal_id)
    if not isinstance(principal, str):
        principal = (
            verification.solver_principal_id
            if isinstance(verification.solver_principal_id, str)
            else None
        )
    pr = verification.pr_number
    if isinstance(pr, bool) or not isinstance(pr, int):
        raw_pr = inputs.get("pr")
        pr = raw_pr if isinstance(raw_pr, int) and not isinstance(raw_pr, bool) else 1
    alert = verification.alert_ref
    if alert is None and isinstance(inputs.get("alert_ref"), str):
        alert = inputs.get("alert_ref")
    return await request_verification(
        db,
        repo_full_name=verification.repo_full_name,
        pr=pr,
        base_sha=verification.base_sha,
        fix_sha=verification.fix_sha,
        advisory=advisory,
        alert_ref=alert if isinstance(alert, str) else None,
        solver_kind=verification.solver_kind,
        solver_principal_id=principal,
        user=user,
        replay=True,
    )
