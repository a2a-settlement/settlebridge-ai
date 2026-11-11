"""Public fix-verification API.

The router is mounted at ``/api/v1/fix-verifications``. Include it from
``app.main`` (this module does not edit ``main``) with::

    from app.routes import fix_verifications
    app.include_router(fix_verifications.router)

Every route is 404 while ``FIX_VERIFIER_PUBLIC_API`` is false, including for
anonymous callers. Creating and replaying also require ``FIX_VERIFIER_ENABLED``.

Callers authenticate with a SettleBridge user session. When
``FIX_VERIFIER_OIDC_ENABLED`` and ``FIX_VERIFIER_PUBLIC_API`` are both true, a
GitHub Actions OIDC bearer for ``https://token.actions.githubusercontent.com``
is also accepted on create, read, and receipt. The token's ``repository`` must
match the verification. Replay stays on the installation owner or an operator.

GET is limited to the GitHub installation owner, the repo-link funder, and
platform operators (``PLATFORM_ADMIN_USER_IDS``). An ``incomplete_fix_confirmed``
result with ``embargo_state`` ``private`` keeps its ``result`` body for the
installation owner and operators; a funder receives the verdict only. An OIDC
caller sees the installation owner's body for that repository. Replay is the
installation owner or an operator.

``request_verification_receipt`` is the mediator hook used by the receipt
route. It does not fetch a receipt.
"""

from __future__ import annotations

import inspect
import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user, is_platform_admin
from app.models.fix_verifier import FixVerification
from app.models.github import GitHubInstallation, GitHubRepoLink
from app.schemas.fix_verification import FixVerificationCreate, FixVerificationResponse
from app.services.disclosure import verifier_may_run
from app.services.fix_verifier import (
    FeatureDisabled,
    InvalidVerificationRequest,
    NotOptedIn,
    access_context,
    replay_verification,
    request_verification,
    request_verification_receipt,
    viewer_can_read,
    viewer_can_replay,
    viewer_can_see_details,
)
from app.services.github_leases import attribute_fix_verification
from app.services.github_oidc import (
    GITHUB_ACTIONS_ISSUER,
    GitHubActionsClaims,
    GitHubOidcError,
    unverified_issuer,
    verify_github_actions_token,
)
from app.services.solver_identity import principals_for_house_account
from app.services.verifier_quota import QuotaExceeded

router = APIRouter(prefix="/api/v1/fix-verifications", tags=["fix-verifications"])

# pull_request OIDC `sha` is the merge commit, not the head the Action sends.
_PULL_REQUEST_EVENTS = frozenset({"pull_request", "pull_request_target"})
_optional_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class _Caller:
    user: object | None
    oidc: GitHubActionsClaims | None = None


async def _require_public_api() -> None:
    if not settings.FIX_VERIFIER_PUBLIC_API:
        raise HTTPException(status_code=404, detail="not_found")


def _github_oidc_accepted() -> bool:
    return bool(settings.FIX_VERIFIER_OIDC_ENABLED) and bool(settings.FIX_VERIFIER_PUBLIC_API)


def _bearer_value(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials is None:
        return None
    token = credentials.credentials
    if not isinstance(token, str) or token == "":
        return None
    return token


async def _session_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
) -> object:
    """SettleBridge session, including a test override of ``get_current_user``."""
    override = request.app.dependency_overrides.get(get_current_user)
    if override is not None:
        user = override()
        if inspect.isawaitable(user):
            user = await user
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await get_current_user(credentials=credentials, db=db)


async def _caller_from_oidc(token: str) -> _Caller:
    try:
        claims = await verify_github_actions_token(token)
    except GitHubOidcError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    return _Caller(user=None, oidc=claims)


async def _authorized_caller(
    request: Request,
    _public: None = Depends(_require_public_api),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db),
) -> _Caller:
    token = _bearer_value(credentials)
    github_issuer = token is not None and unverified_issuer(token) == GITHUB_ACTIONS_ISSUER
    if _github_oidc_accepted() and github_issuer:
        return await _caller_from_oidc(token)
    return _Caller(user=await _session_user(request, credentials, db))


def _dump(row: FixVerification) -> dict:
    body = FixVerificationResponse.model_validate(row).model_dump(mode="json")
    if not isinstance(body.get("result"), dict):
        body["result"] = {}
    return body


def _redact_details(body: dict) -> dict:
    verdict = body.get("verdict")
    redacted = dict(body)
    redacted["result"] = {"verdict": verdict} if isinstance(verdict, str) else {}
    return redacted


def _visible_body(
    row: FixVerification, user_id, link, installation, *, direct: bool
) -> dict | None:
    if not direct and not viewer_can_read(user_id, row, link, installation):
        return None
    body = _dump(row)
    if not viewer_can_see_details(user_id, row, installation):
        return _redact_details(body)
    return body


def _house_principals() -> frozenset[str]:
    raw = settings.PLATFORM_HOUSE_PRINCIPALS or ""
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


async def _attributed_solver_kind(db: AsyncSession, user: object, payload: FixVerificationCreate):
    """Solver kind for the receipt. A caller can lower the claim, never raise it.

    House callers (platform admins, ``account_kind == "house"``) are always house.
    ``third_party`` is kept only when ``attribute_fix_verification`` agrees.
    """
    if (
        payload.solver_kind == "house"
        or is_platform_admin(user)
        or principals_for_house_account(user)
    ):
        return "house", None
    if payload.solver_kind != "third_party" or not payload.solver_principal_id:
        return "unknown", None
    attribution = await attribute_fix_verification(
        payload.solver_principal_id, house_principals=_house_principals(), db=db
    )
    return attribution.solver_kind, attribution.denial


async def _solver_kind_for_request(db: AsyncSession, caller: _Caller, payload: FixVerificationCreate):
    """OIDC callers stay unknown unless they explicitly lower the claim to house."""
    if caller.oidc is not None:
        if payload.solver_kind == "house":
            return "house", None
        return "unknown", None
    return await _attributed_solver_kind(db, caller.user, payload)


def _names_match(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return left.casefold() == right.casefold()


def _oidc_sha_allowed(claims: GitHubActionsClaims, fix_sha: str) -> bool:
    if claims.event_name in _PULL_REQUEST_EVENTS:
        return True
    return fix_sha.casefold() == claims.sha.casefold()


def _owner_id(installation: GitHubInstallation | None) -> object:
    if installation is None:
        return None
    return installation.owner_user_id


def _viewer_id(caller: _Caller, row: FixVerification | None, installation) -> object:
    """User id for a read. OIDC is limited to the token's repository."""
    if caller.oidc is None:
        return caller.user.id
    if row is None or not _names_match(row.repo_full_name, caller.oidc.repository):
        raise HTTPException(status_code=404, detail="not_found")
    return _owner_id(installation)


async def _link_and_installation(db: AsyncSession, repo_full_name: str):
    key = repo_full_name.strip().lower()
    if not key:
        return None, None
    link_stmt = (
        select(GitHubRepoLink).where(func.lower(GitHubRepoLink.repo_full_name) == key).limit(1)
    )
    link = (await db.execute(link_stmt)).scalar_one_or_none()
    if link is None:
        return None, None
    inst_stmt = select(GitHubInstallation).where(
        GitHubInstallation.installation_id == link.installation_id
    )
    installation = (await db.execute(inst_stmt)).scalar_one_or_none()
    return link, installation


async def _require_oidc_owner(db: AsyncSession, repo_full_name: str) -> SimpleNamespace:
    link, installation = await _link_and_installation(db, repo_full_name)
    if (
        link is None
        or installation is None
        or not verifier_may_run(link)
        or installation.owner_user_id is None
    ):
        raise HTTPException(status_code=403, detail="not_opted_in")
    return SimpleNamespace(id=installation.owner_user_id)


async def _load(db: AsyncSession, verification_id: uuid.UUID) -> FixVerification | None:
    stmt = select(FixVerification).where(FixVerification.id == verification_id)
    return (await db.execute(stmt)).scalar_one_or_none()


@router.post("")
async def create_fix_verification(
    request: Request,
    caller: _Caller = Depends(_authorized_caller),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = FixVerificationCreate.model_validate(await request.json())
    except (json.JSONDecodeError, ValidationError, UnicodeError):
        raise HTTPException(status_code=422, detail="invalid_request") from None
    user = caller.user
    if caller.oidc is not None:
        if not _names_match(payload.repo_full_name, caller.oidc.repository):
            raise HTTPException(status_code=403, detail="repo_mismatch")
        if not _oidc_sha_allowed(caller.oidc, payload.fix_sha):
            raise HTTPException(status_code=403, detail="sha_mismatch")
        user = await _require_oidc_owner(db, payload.repo_full_name)
    try:
        solver_kind, denial = await _solver_kind_for_request(db, caller, payload)
        if denial is not None:
            return JSONResponse(status_code=denial.status_code, content=denial.body)
        row = await request_verification(
            db,
            repo_full_name=payload.repo_full_name,
            pr=payload.pr,
            base_sha=payload.base_sha,
            fix_sha=payload.fix_sha,
            advisory=payload.advisory.model_dump(),
            alert_ref=payload.alert_ref,
            solver_kind=solver_kind,
            solver_principal_id=payload.solver_principal_id,
            user=user,
        )
    except FeatureDisabled:
        raise HTTPException(status_code=404, detail="not_found") from None
    except NotOptedIn:
        raise HTTPException(status_code=403, detail="not_opted_in") from None
    except InvalidVerificationRequest as exc:
        raise HTTPException(status_code=422, detail=exc.code) from None
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="quota_exceeded",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    link, installation = await access_context(db, row)
    await db.commit()
    body = _visible_body(row, user.id, link, installation, direct=True)
    return JSONResponse(status_code=201, content=body)


@router.get("/{verification_id}")
async def get_fix_verification(
    verification_id: uuid.UUID,
    caller: _Caller = Depends(_authorized_caller),
    db: AsyncSession = Depends(get_db),
):
    row = await _load(db, verification_id)
    link, installation = (None, None)
    if row is not None:
        link, installation = await access_context(db, row)
    viewer_id = _viewer_id(caller, row, installation)
    body = None if row is None else _visible_body(row, viewer_id, link, installation, direct=False)
    if body is None:
        raise HTTPException(status_code=404, detail="not_found")
    return body


@router.get("/{verification_id}/receipt")
async def get_fix_verification_receipt(
    verification_id: uuid.UUID,
    caller: _Caller = Depends(_authorized_caller),
    db: AsyncSession = Depends(get_db),
):
    row = await _load(db, verification_id)
    link, installation = (None, None)
    if row is not None:
        link, installation = await access_context(db, row)
    viewer_id = _viewer_id(caller, row, installation)
    if row is None or not viewer_can_read(viewer_id, row, link, installation):
        raise HTTPException(status_code=404, detail="not_found")
    receipt_id = await request_verification_receipt(db, row)
    if receipt_id is None:
        raise HTTPException(status_code=404, detail="not_found")
    return {"receipt_id": receipt_id}


@router.post("/{verification_id}/replay")
async def replay_fix_verification(
    verification_id: uuid.UUID,
    caller: _Caller = Depends(_authorized_caller),
    db: AsyncSession = Depends(get_db),
):
    if caller.oidc is not None:
        raise HTTPException(status_code=403, detail="forbidden")
    user = caller.user
    row = await _load(db, verification_id)
    link, installation = (None, None)
    if row is not None:
        link, installation = await access_context(db, row)
    if row is None or not viewer_can_replay(user.id, link, installation):
        raise HTTPException(status_code=404, detail="not_found")
    try:
        updated = await replay_verification(db, row, user=user)
    except FeatureDisabled:
        raise HTTPException(status_code=404, detail="not_found") from None
    except NotOptedIn:
        raise HTTPException(status_code=403, detail="not_opted_in") from None
    except InvalidVerificationRequest as exc:
        raise HTTPException(status_code=422, detail=exc.code) from None
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="quota_exceeded",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    link, installation = await access_context(db, updated)
    await db.commit()
    body = _visible_body(updated, user.id, link, installation, direct=False)
    if body is None:
        raise HTTPException(status_code=404, detail="not_found")
    return body
