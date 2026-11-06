"""Self-serve repo onboarding for a signed-in repo admin who funds the repo.

Off unless ``GITHUB_SELF_SERVE_ONBOARDING`` is true. The caller is always the
funder. Their linked GitHub identity must hold ``admin`` on the repo, checked by
GitHub user id and not only by login, because logins can be renamed. Required
checks must be passed explicitly; the preview shows what GitHub reports.
``auto_dispatch_alerts``, ``auto_merge_acceptable`` and ``strict_tests`` are
optional. Omitted, each stays as it is. Alert dispatch and auto-merge still
depend on the matching global setting. A caller with linked GitHub is warned,
without blocking, that pull requests from that account cannot claim bounties
they fund.

Errors carry ``detail = {"code", "message", ...}`` with a stable ``code``. Nothing
about a repo is returned until the caller is shown to be its admin, so a missing
App installation and a repo the caller cannot administer look the same.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubIdentity, GitHubRepoLink
from app.models.user import User
from app.services import exchange, github_app
from app.services.github_bounty_service import list_repo_links_for_installation
from app.services.github_identity import get_identity_for_user
from app.services.github_login import ensure_exchange_account
from app.services.github_onboarding import (
    FunderConflictError,
    OnboardError,
    OnboardExchangeError,
    OnboardGitHubError,
    detect_defaults,
    error_code,
    error_data,
    funder_headroom,
    minimum_headroom,
    onboard_repo,
)
from app.services.github_repo_admin import (
    RepoAdminError,
    _installation_row,
    _require_link,
    _strict_tests_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_INSTALLATION_REPOS = 50


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    app_id: int


class OnboardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checks: list[CheckBody] = Field(min_length=1)
    allowed_paths: list[str] | None = None
    allowed_dependency_changes: list[str] | None = None
    auto_dispatch_alerts: bool | None = None
    auto_merge_acceptable: bool | None = None
    strict_tests: bool | None = None
    dry_run: bool = False


def _error(status_code: int, code: str, message: str, **data) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **data})


def _urls() -> dict:
    market = settings.MARKET_URL.rstrip("/")
    return {
        "install_app": f"https://github.com/apps/{settings.GITHUB_APP_SLUG}/installations/new",
        "link_github": f"{market}/dashboard/settings/github",
        "setup_page": f"{market}/dashboard/github/setup",
    }


def _require_enabled() -> None:
    if not settings.GITHUB_SELF_SERVE_ONBOARDING:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def _repo_name(owner: str, name: str) -> str:
    if _SEGMENT_RE.fullmatch(owner) is None or _SEGMENT_RE.fullmatch(name) is None:
        raise _error(422, "invalid_repo", "invalid repository name")
    return f"{owner}/{name}"


async def _live_link(db: AsyncSession, repo: str) -> GitHubRepoLink | None:
    try:
        link = await _require_link(db, repo)
    except RepoAdminError:
        return None
    installation = await _installation_row(db, int(link.installation_id))
    if installation is None or installation.removed_at is not None:
        return None
    return link


async def _is_admin(link: GitHubRepoLink, identity: GitHubIdentity) -> bool:
    try:
        permission, github_user_id = await github_app.get_collaborator_record(
            int(link.installation_id), link.repo_full_name, identity.login
        )
    except github_app.GitHubAPIError as exc:
        if exc.status_code in (403, 404):
            return False
        logger.warning("collaborator lookup failed for %s", link.repo_full_name)
        raise _error(
            status.HTTP_502_BAD_GATEWAY, "github_unavailable", "GitHub is unavailable"
        ) from exc
    return permission == "admin" and github_user_id == int(identity.github_user_id)


async def _authorized_link(db: AsyncSession, user: User, repo: str) -> GitHubRepoLink:
    _require_enabled()
    identity = await get_identity_for_user(db, user.id)
    if identity is None:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "link_github",
            "link your GitHub account first",
            url=_urls()["link_github"],
        )
    link = await _live_link(db, repo)
    if link is None or not await _is_admin(link, identity):
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "repo_access",
            f"install the SettleBridge GitHub App on {repo} and sign in to GitHub as a repo admin",
            url=_urls()["install_app"],
            github_login=identity.login,
        )
    return link


def _service_error(exc: RepoAdminError) -> HTTPException:
    if isinstance(exc, FunderConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, (OnboardGitHubError, OnboardExchangeError)):
        status_code = status.HTTP_502_BAD_GATEWAY
    else:
        status_code = 422
    return _error(status_code, error_code(exc), str(exc), **error_data(exc))


def _step(code: str, message: str, *, blocking: bool = True, **data) -> dict:
    return {"code": code, "message": message, "blocking": blocking, **data}


def _repo_options(link: GitHubRepoLink) -> dict:
    policy = link.default_policy if isinstance(link.default_policy, dict) else {}
    return {
        "auto_dispatch_alerts": bool(link.auto_dispatch_alerts),
        "auto_merge_acceptable": bool(link.auto_merge_acceptable),
        "strict_tests": _strict_tests_enabled(policy),
    }


def _funder_cannot_solve_step(login: str) -> dict:
    return _step(
        "funder_cannot_solve",
        "You will fund this repo's bounties, so pull requests from your GitHub "
        f"account (@{login}) cannot claim them. Ask another account to fund the "
        "repo if you want to solve them yourself.",
        blocking=False,
    )


async def _funding_steps(db: AsyncSession, user: User) -> tuple[dict, list[dict]]:
    minimum = minimum_headroom()
    funder = {
        "exchange_account": bool(user.exchange_bot_id),
        "headroom_ate": None,
        "minimum_ate": minimum,
    }
    funding = bool(settings.GITHUB_SETUP_FUNDING_ENABLED)
    extra = {"action": "add_funds", "url": _urls()["setup_page"]} if funding else {}
    if not user.exchange_bot_id:
        return funder, [
            _step(
                "funder_no_exchange",
                "create and fund an exchange account with Add funds on the setup page"
                if funding
                else "create an exchange account and sign in to SettleBridge with its API key",
                **extra,
            )
        ]
    try:
        headroom = await funder_headroom(db, user)
    except OnboardError as exc:
        raise _service_error(exc) from exc
    funder["headroom_ate"] = headroom
    if headroom < minimum:
        return funder, [
            _step(
                "funder_low_balance",
                f"fund your exchange account: {headroom} ATE free, at least {minimum} ATE needed"
                + (" (use Add funds on the setup page)" if funding else ""),
                headroom_ate=headroom,
                minimum_ate=minimum,
                **extra,
            )
        ]
    return funder, []


@router.get("/repos/{owner}/{name}/setup-status")
async def setup_status(
    owner: str,
    name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Every step still needed before ``POST .../onboard`` would succeed, with links."""
    _require_enabled()
    repo = _repo_name(owner, name)
    urls = _urls()
    identity = await get_identity_for_user(db, user.id)
    steps: list[dict] = []
    result: dict = {
        "repo": repo,
        "ready": False,
        "enabled": None,
        "funded_by_you": None,
        "funded_by_other": None,
        "identity": {"linked": identity is not None, "login": identity.login if identity else None},
        "repo_access": False,
        "funder": None,
        "detected": None,
        "auto_dispatch_alerts": None,
        "auto_merge_acceptable": None,
        "strict_tests": None,
        "urls": urls,
    }
    if identity is None:
        steps.append(
            _step(
                "link_github",
                "link your GitHub account in SettleBridge (one browser visit)",
                url=urls["link_github"],
            )
        )
    else:
        link = await _live_link(db, repo)
        if link is not None and await _is_admin(link, identity):
            result["repo_access"] = True
            funded_by_other = link.funder_user_id is not None and link.funder_user_id != user.id
            result["enabled"] = bool(link.enabled)
            result["funded_by_you"] = link.funder_user_id == user.id
            result["funded_by_other"] = funded_by_other
            result.update(_repo_options(link))
            if funded_by_other:
                steps.append(_step("funded_by_other", f"{repo} is already funded by another user"))
            try:
                detected = await detect_defaults(db, repo)
            except RepoAdminError as exc:
                raise _service_error(exc) from exc
            result["detected"] = detected
            if not detected["required_checks"]:
                steps.append(
                    _step(
                        "no_pinned_check",
                        "choose the required check that must pass; none is pinned to an app "
                        f"on {detected['default_branch']}",
                        candidates=detected["candidate_checks"],
                    )
                )
            if not detected["allowed_paths"]:
                steps.append(
                    _step(
                        "needs_allowed_path",
                        "choose the paths a solver may change, such as src/*",
                    )
                )
            if link.enabled and result["funded_by_you"]:
                steps.append(
                    _step("already_enabled", f"{repo} already takes bounties", blocking=False)
                )
        else:
            steps.append(
                _step(
                    "repo_access",
                    f"install the SettleBridge GitHub App on {repo} and make sure "
                    f"{identity.login} is a repo admin",
                    url=urls["install_app"],
                )
            )
    result["funder"], funding = await _funding_steps(db, user)
    steps.extend(funding)
    if identity is not None:
        steps.append(_funder_cannot_solve_step(identity.login))
    result["next_steps"] = steps
    result["ready"] = not any(step["blocking"] for step in steps)
    return result


@router.get("/setup/info")
async def setup_info(user: User = Depends(get_current_user)) -> dict:
    """Links and options for the setup page before a repo is chosen."""
    _require_enabled()
    return {
        "urls": _urls(),
        "add_funds": {
            "enabled": bool(settings.GITHUB_SETUP_FUNDING_ENABLED),
            "amount_ate": int(settings.GITHUB_SETUP_FUND_ATE),
            "max_balance_ate": int(settings.GITHUB_SETUP_FUND_MAX_BALANCE_ATE),
        },
    }


@router.post("/setup/add-funds")
async def add_funds(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Deposit test ATE into the caller's own exchange account, creating it if needed."""
    _require_enabled()
    if not settings.GITHUB_SETUP_FUNDING_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    amount = int(settings.GITHUB_SETUP_FUND_ATE)
    cap = int(settings.GITHUB_SETUP_FUND_MAX_BALANCE_ATE)
    if not await ensure_exchange_account(db, user):
        await db.rollback()
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "exchange_unavailable",
            "could not create an exchange account",
        )
    await db.commit()
    try:
        dashboard = await asyncio.to_thread(exchange.get_balance_dashboard, user.exchange_bot_id)
        balance = int(dashboard["available"])
    except Exception as exc:
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "exchange_unavailable",
            "could not read your exchange balance",
        ) from exc
    if balance >= cap:
        raise _error(
            status.HTTP_409_CONFLICT,
            "balance_sufficient",
            f"your balance is {balance} ATE; Add funds stops at {cap} ATE",
            balance_ate=balance,
            max_balance_ate=cap,
        )
    try:
        result = await asyncio.to_thread(
            exchange.deposit, user, amount, "settlebridge-github-setup"
        )
    except Exception as exc:
        logger.warning("setup deposit failed for user %s", user.id)
        raise _error(
            status.HTTP_502_BAD_GATEWAY, "exchange_unavailable", "the exchange refused the deposit"
        ) from exc
    new_balance = result.get("new_balance") if isinstance(result, dict) else None
    return {
        "deposited_ate": amount,
        "balance_ate": int(new_balance) if isinstance(new_balance, int) else balance + amount,
    }


@router.get("/setup/installations/{installation_id}/repos")
async def installation_repos(
    installation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Repos in an installation that the caller administers. Others are left out."""
    _require_enabled()
    identity = await get_identity_for_user(db, user.id)
    if identity is None:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "link_github",
            "link your GitHub account first",
            url=_urls()["link_github"],
        )
    repos: list[dict] = []
    installation = await _installation_row(db, installation_id)
    links: list[GitHubRepoLink] = []
    if installation is not None and installation.removed_at is None:
        links = sorted(
            await list_repo_links_for_installation(db, installation_id),
            key=lambda link: link.repo_full_name.casefold(),
        )
    truncated = len(links) > MAX_INSTALLATION_REPOS
    for link in links[:MAX_INSTALLATION_REPOS]:
        if not await _is_admin(link, identity):
            continue
        repos.append(
            {
                "repo": link.repo_full_name,
                "enabled": bool(link.enabled),
                "funded_by_you": link.funder_user_id == user.id,
                "funded_by_other": link.funder_user_id is not None
                and link.funder_user_id != user.id,
            }
        )
    return {"github_login": identity.login, "repos": repos, "truncated": truncated}


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
        "funder_cannot_solve": True,
        **_repo_options(link),
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
            auto_dispatch_alerts=body.auto_dispatch_alerts,
            auto_merge_acceptable=body.auto_merge_acceptable,
            strict_tests=body.strict_tests,
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
