"""Self-serve repo onboarding for a signed-in repo admin who funds the repo.

Off unless ``GITHUB_SELF_SERVE_ONBOARDING`` is true. The caller is always the
funder. Their linked GitHub identity must hold ``admin`` on the repo, checked by
GitHub user id and not only by login, because logins can be renamed. Required
checks must be passed explicitly; the preview shows what GitHub reports.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubRepoLink
from app.models.user import User
from app.services import github_app
from app.services.github_identity import get_identity_for_user
from app.services.github_onboarding import (
    FunderConflictError,
    OnboardExchangeError,
    OnboardGitHubError,
    detect_defaults,
    onboard_repo,
)
from app.services.github_repo_admin import RepoAdminError, _require_link

logger = logging.getLogger(__name__)

router = APIRouter()

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    app_id: int


class OnboardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[CheckBody] = Field(min_length=1)
    allowed_paths: list[str] | None = None
    allowed_dependency_changes: list[str] | None = None
    dry_run: bool = False


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _repo_name(owner: str, name: str) -> str:
    if _SEGMENT_RE.fullmatch(owner) is None or _SEGMENT_RE.fullmatch(name) is None:
        raise _error(422, "invalid repository name")
    return f"{owner}/{name}"


async def _authorized_link(db: AsyncSession, user: User, repo: str) -> GitHubRepoLink:
    if not settings.GITHUB_SELF_SERVE_ONBOARDING:
        raise _error(status.HTTP_404_NOT_FOUND, "Not Found")
    identity = await get_identity_for_user(db, user.id)
    if identity is None:
        raise _error(status.HTTP_403_FORBIDDEN, "link your GitHub account first")
    try:
        link = await _require_link(db, repo)
    except RepoAdminError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "install the GitHub App on this repo first") from exc
    try:
        permission, github_user_id = await github_app.get_collaborator_record(
            int(link.installation_id), link.repo_full_name, identity.login
        )
    except github_app.GitHubAPIError as exc:
        logger.warning("collaborator lookup failed for %s", link.repo_full_name)
        raise _error(status.HTTP_502_BAD_GATEWAY, "GitHub is unavailable") from exc
    if permission != "admin" or github_user_id != int(identity.github_user_id):
        raise _error(status.HTTP_403_FORBIDDEN, "repo admin permission is required")
    return link


def _service_error(exc: RepoAdminError) -> HTTPException:
    if isinstance(exc, FunderConflictError):
        return _error(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, (OnboardGitHubError, OnboardExchangeError)):
        return _error(status.HTTP_502_BAD_GATEWAY, str(exc))
    return _error(422, str(exc))


@router.get("/repos/{owner}/{name}/onboard")
async def preview_onboarding(
    owner: str,
    name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    repo = _repo_name(owner, name)
    link = await _authorized_link(db, user, repo)
    try:
        detected = await detect_defaults(db, repo)
    except RepoAdminError as exc:
        raise _service_error(exc) from exc
    return {
        "detected": detected,
        "enabled": bool(link.enabled),
        "funded_by_you": link.funder_user_id == user.id,
        "funded_by_other": link.funder_user_id is not None and link.funder_user_id != user.id,
    }


@router.post("/repos/{owner}/{name}/onboard")
async def onboard(
    owner: str,
    name: str,
    body: OnboardBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    repo = _repo_name(owner, name)
    await _authorized_link(db, user, repo)
    try:
        result = await onboard_repo(
            db,
            repo,
            funder=user,
            checks=[check.model_dump() for check in body.checks],
            allowed_paths=body.allowed_paths,
            allowed_dependency_changes=body.allowed_dependency_changes,
            actor_user_id=user.id,
            create_labels=not body.dry_run,
        )
    except RepoAdminError as exc:
        await db.rollback()
        raise _service_error(exc) from exc
    if body.dry_run or not result["changed"]:
        await db.rollback()
    else:
        await db.commit()
    return {**result, "dry_run": body.dry_run}
