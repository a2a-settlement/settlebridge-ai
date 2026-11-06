"""Record GitHub repository visibility and cancel bounties on repos that are not public.

Run from the backend directory::

    PYTHONPATH=. python -m app.cli.github_visibility backfill
    PYTHONPATH=. python -m app.cli.github_visibility backfill --dry-run

Each linked repo is read from GitHub. Visibility is stored. Advertised bindings
on a repo that is not public are cancelled when they have no active lease.
A work or submitted lease is left in place and counted. ``--dry-run`` reads
GitHub and prints the same counts, and writes nothing.

Output is counts and repository names only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.github import (
    BindingState,
    GitHubBountyBinding,
    GitHubRepoLink,
    is_public_visibility,
)
from app.services import github_app, github_bounty_service
from app.services.github_bounty_service import (
    REASON_NOT_PUBLIC,
    get_active_lease,
    visibility_from_repository,
)

_LIVE_STATES = (
    BindingState.ADVERTISED.value,
    BindingState.EVALUATING.value,
    BindingState.NEEDS_REVIEW.value,
    BindingState.AWAITING_MERGE.value,
)


class VisibilityError(Exception):
    """The backfill stopped. The message is safe to print."""


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


async def _dispatch(args: argparse.Namespace) -> int:
    async with async_session() as db:
        try:
            payload = await backfill(db, dry_run=args.dry_run)
        except VisibilityError as exc:
            await db.rollback()
            print(str(exc), file=sys.stderr)
            return 2
        except Exception:
            await db.rollback()
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
