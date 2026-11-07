"""Record GitHub repository visibility and re-sync bounty issues.

Run from the backend directory::

    PYTHONPATH=. python -m app.cli.github_visibility backfill
    PYTHONPATH=. python -m app.cli.github_visibility backfill --dry-run
    PYTHONPATH=. python -m app.cli.github_visibility readvertise --repo OWNER/NAME
    PYTHONPATH=. python -m app.cli.github_visibility readvertise --repo OWNER/NAME --dry-run

``backfill`` reads each linked repo from GitHub and stores visibility. Advertised
bindings on a repo that is not public are cancelled when they have no active lease.
A work or submitted lease is left in place and counted. ``--dry-run`` reads
GitHub and prints the same counts, and writes nothing. Output is counts and
repository names only.

``readvertise`` re-syncs open issues that still carry a bounty label on one
enabled, funded repo, so a binding cancelled by the old public-only rule can
be advertised again. Output is counts and issue numbers only.
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import json
import re
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.bounty import BountyStatus
from app.models.github import (
    BindingState,
    GitHubBountyBinding,
    GitHubJob,
    GitHubRepoLink,
    JobKind,
    is_public_visibility,
)
from app.services import github_app, github_bounty_service
from app.services.github_bounty_service import (
    REASON_NOT_PUBLIC,
    get_active_lease,
    get_binding_by_issue,
    parse_bounty_labels,
    visibility_from_repository,
)

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_KNOWN_VISIBILITY = frozenset({"public", "private", "internal"})
_SETTLED_BINDINGS = frozenset(
    {BindingState.SETTLEMENT_PENDING.value, BindingState.PAID.value}
)

_LIVE_STATES = (
    BindingState.ADVERTISED.value,
    BindingState.EVALUATING.value,
    BindingState.NEEDS_REVIEW.value,
    BindingState.AWAITING_MERGE.value,
)


class VisibilityError(Exception):
    """The backfill stopped. The message is safe to print."""


def _repo_arg(value: str) -> str:
    if _REPO_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("invalid repository name")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.github_visibility",
        description="Record GitHub repo visibility and cancel bounties that are not public.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser(
        "backfill",
        help="Read each linked repo, store visibility, and cancel unleased non-public bounties.",
    )
    backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only. Do not store visibility or cancel bindings.",
    )
    readvertise = sub.add_parser(
        "readvertise",
        help="Re-sync open bounty issues so a cancelled private-repo binding can return.",
    )
    readvertise.add_argument(
        "--repo", required=True, type=_repo_arg, help="GitHub repo as OWNER/NAME"
    )
    readvertise.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts and issue numbers only. Do not write.",
    )
    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _bucket(visibility: str | None) -> str:
    if visibility in {"public", "private", "internal"}:
        return visibility
    return "unknown"


async def _live_bindings(db: AsyncSession, repo: str) -> list[GitHubBountyBinding]:
    stmt = (
        select(GitHubBountyBinding)
        .where(
            GitHubBountyBinding.repo_full_name == repo,
            GitHubBountyBinding.state.in_(_LIVE_STATES),
        )
        .order_by(GitHubBountyBinding.issue_number)
    )
    return list((await db.execute(stmt)).scalars().all())


async def backfill(db: AsyncSession, *, dry_run: bool) -> dict:
    """Read every repo link. Store visibility and cancel unleased live bindings unless dry-run."""
    stmt = select(GitHubRepoLink).order_by(GitHubRepoLink.repo_full_name)
    links = list((await db.execute(stmt)).scalars().all())
    summary = {
        "public": 0,
        "private": 0,
        "internal": 0,
        "unknown": 0,
        "cancelled": 0,
        "active_lease": 0,
        "repos": [],
    }
    for link in links:
        repo = link.repo_full_name
        summary["repos"].append(repo)
        try:
            payload = await github_app.get_repository(int(link.installation_id), repo)
        except Exception as exc:
            raise VisibilityError(f"GitHub read failed for {repo}") from exc
        visibility = visibility_from_repository(payload)
        if not dry_run:
            link.visibility = visibility
        summary[_bucket(visibility)] += 1
        if is_public_visibility(visibility):
            continue
        for binding in await _live_bindings(db, repo):
            if await get_active_lease(db, binding.id) is not None:
                summary["active_lease"] += 1
                continue
            summary["cancelled"] += 1
            if dry_run:
                continue
            await github_bounty_service._cancel_binding(
                db, binding, int(link.installation_id), REASON_NOT_PUBLIC
            )
    return summary


def _enum_text(value: object) -> str:
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


def _issue_number(issue: dict) -> int | None:
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        return None
    return number


def _labeled_job(link: GitHubRepoLink, issue: dict) -> GitHubJob:
    """In-memory labeled event. Not added to the session."""
    return GitHubJob(
        kind=JobKind.ISSUE_LABELED.value,
        payload={
            "event": "issues",
            "action": "labeled",
            "data": {
                "issue": issue,
                "repository": {
                    "full_name": link.repo_full_name,
                    "visibility": link.visibility,
                },
                "installation": {"id": int(link.installation_id)},
            },
        },
    )


async def _open_issues(installation_id: int, repo: str) -> list[dict]:
    """Open issues, excluding pull requests.

    Same paged GET ``github_app.find_issue_with_marker`` uses, with ``state=open``.
    """
    repo = github_app._validate_repo(repo)
    token = await github_app.installation_token(installation_id)
    url = f"{github_app._api_root()}/repos/{repo}/issues?state=open&per_page=100"
    found: list[dict] = []
    async with github_app._client() as client:
        for _page in range(github_app._ISSUE_LIST_PAGES):
            response = await github_app._send(client, "GET", url, token)
            github_app._ensure_ok(response)
            payload = github_app._read_json(response)
            if not isinstance(payload, list):
                raise github_app.GitHubAPIError("unexpected issue list", response.status_code)
            for item in payload:
                if isinstance(item, dict) and "pull_request" not in item:
                    found.append(item)
            nxt = github_app._next_url(response)
            if nxt is None:
                return found
            url = nxt
    raise github_app.GitHubAPIError("issue search reached the page limit")


async def _enabled_funded_link(db: AsyncSession, repo: str) -> GitHubRepoLink | None:
    stmt = select(GitHubRepoLink).where(
        func.lower(GitHubRepoLink.repo_full_name) == repo.casefold(),
        GitHubRepoLink.enabled.is_(True),
        GitHubRepoLink.funder_user_id.is_not(None),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _bounty_settled(bounty: object) -> bool:
    if bounty is None:
        return False
    return _enum_text(getattr(bounty, "status", "")) == BountyStatus.COMPLETED.value


async def _readvertise_issue(
    db: AsyncSession,
    link: GitHubRepoLink,
    issue: dict,
    number: int,
    summary: dict,
    *,
    dry_run: bool,
) -> None:
    summary["issue_numbers"].append(number)
    binding = await get_binding_by_issue(db, link.repo_full_name, number)
    if binding is None:
        summary["would_create"].append(number)
        if not dry_run:
            await github_bounty_service._sync_issue_terms(db, _labeled_job(link, issue))
        return
    if await get_active_lease(db, binding.id) is not None:
        summary["skipped"].append(number)
        return
    bounty = await github_bounty_service.get_bounty(db, binding.bounty_id)
    settled = _enum_text(binding.state) in _SETTLED_BINDINGS or _bounty_settled(bounty)
    if settled:
        summary["skipped"].append(number)
        return
    if _enum_text(binding.state) == BindingState.CANCELLED.value:
        summary["would_revive"].append(number)
        if dry_run:
            return
        binding.state = BindingState.ADVERTISED.value
        if bounty is not None:
            bounty.status = BountyStatus.OPEN
        await db.flush()
        await github_bounty_service._sync_issue_terms(db, _labeled_job(link, issue))
        return
    # Evaluating, awaiting merge, and settlement stay as they are. Refreshing
    # them would rebuild terms under an in-progress review.
    if _enum_text(binding.state) != BindingState.ADVERTISED.value:
        summary["skipped"].append(number)
        return
    if not dry_run:
        await github_bounty_service._sync_issue_terms(db, _labeled_job(link, issue))


async def readvertise(db: AsyncSession, *, repo: str, dry_run: bool) -> dict:
    """Re-sync open bounty issues on one enabled, funded repo. One commit, by the caller."""
    if _REPO_RE.fullmatch(repo) is None:
        raise VisibilityError("invalid repository name")
    link = await _enabled_funded_link(db, repo)
    if link is None:
        raise VisibilityError("no enabled funded repository link")
    if link.visibility not in _KNOWN_VISIBILITY:
        raise VisibilityError("visibility_unknown")
    try:
        issues = await _open_issues(int(link.installation_id), link.repo_full_name)
    except Exception as exc:
        raise VisibilityError(f"GitHub read failed for {link.repo_full_name}") from exc
    chosen: list[tuple[int, dict]] = []
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        number = _issue_number(issue)
        if number is None:
            continue
        state = issue.get("state")
        if isinstance(state, str) and state != "open":
            continue
        parsed = parse_bounty_labels(github_bounty_service._label_names(issue))
        if parsed.amount_usd is None:
            continue
        chosen.append((number, issue))
    chosen.sort(key=lambda item: item[0])
    summary: dict = {
        "repos": [link.repo_full_name],
        "issue_numbers": [],
        "would_create": [],
        "would_revive": [],
        "skipped": [],
    }
    for number, issue in chosen:
        await _readvertise_issue(db, link, issue, number, summary, dry_run=dry_run)
    return summary


async def _dispatch(args: argparse.Namespace) -> int:
    async with async_session() as db:
        try:
            if args.command == "readvertise":
                payload = await readvertise(db, repo=args.repo, dry_run=args.dry_run)
            else:
                payload = await backfill(db, dry_run=args.dry_run)
        except VisibilityError as exc:
            await db.rollback()
            print(str(exc), file=sys.stderr)
            return 2
        except Exception:
            await db.rollback()
            if getattr(args, "command", None) == "readvertise":
                print("GitHub visibility readvertise failed", file=sys.stderr)
            else:
                print("GitHub visibility backfill failed", file=sys.stderr)
            return 2
        _print_json(payload)
        if args.dry_run:
            await db.rollback()
            print("DRY RUN")
        else:
            await db.commit()
            print("APPLIED")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    raise SystemExit(main())
