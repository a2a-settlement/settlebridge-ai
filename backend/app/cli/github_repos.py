"""Operator commands for GitHub repo links created by the App installation.

Run from the backend directory::

    PYTHONPATH=. python -m app.cli.github_repos list
    PYTHONPATH=. python -m app.cli.github_repos onboard --repo OWNER/NAME --funder-email EMAIL
    PYTHONPATH=. python -m app.cli.github_repos configure --repo OWNER/NAME ...
    PYTHONPATH=. python -m app.cli.github_repos enable --repo OWNER/NAME
    PYTHONPATH=. python -m app.cli.github_repos disable --repo OWNER/NAME

Commands other than ``list`` print the change and roll it back unless
``--apply`` is passed. ``list`` never writes.

Changing a link does not rewrite AcceptanceSpec rows. A bounty copies
``required_checks`` and ``default_policy`` from the link when its issue is
labelled or later edited, and only while that link is enabled and funded.
An open bounty can therefore pick up a later edit on its next issue event.
Settled and cancelled bindings are left alone. A lease keeps the spec it
pinned, and settlement reads that spec.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.database import async_session
from app.services.github_onboarding import onboard_repo
from app.services.github_repo_admin import (
    RepoAdminError,
    _require_user,
    configure_repo_link,
    list_repo_links,
    set_repo_enabled,
)


def parse_check(value: str) -> dict:
    """Parse ``NAME:APP_ID``, splitting on the last colon so names may contain colons."""
    name, separator, raw_app_id = value.rpartition(":")
    if separator == "" or name.strip() == "":
        raise argparse.ArgumentTypeError(
            f"check must be NAME:APP_ID (split on the last colon), got {value!r}"
        )
    if not raw_app_id.isascii() or not raw_app_id.isdecimal():
        raise argparse.ArgumentTypeError(f"APP_ID must be a positive integer, got {raw_app_id!r}")
    app_id = int(raw_app_id)
    if app_id <= 0:
        raise argparse.ArgumentTypeError(f"APP_ID must be a positive integer, got {raw_app_id!r}")
    return {"name": name.strip(), "app_id": app_id}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.github_repos",
        description="Link a GitHub repo to the funder that pays its bounties.",
    )
    write_flags = argparse.ArgumentParser(add_help=False)
    write_flags.add_argument(
        "--apply",
        action="store_true",
        help="Commit the change. Without this flag the command is a dry run.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show repo links. Does not write.")

    onboard = sub.add_parser(
        "onboard",
        parents=[write_flags],
        help=(
            "Detect required checks and allowed paths, then set the funder and enable the "
            "repo in one transaction. Passed values replace detected ones."
        ),
    )
    onboard.add_argument("--repo", required=True, help="GitHub repo as OWNER/NAME")
    onboard.add_argument(
        "--funder-email", required=True, help="User who funds bounties on this repo."
    )
    onboard.add_argument(
        "--check",
        action="append",
        default=None,
        type=parse_check,
        metavar="NAME:APP_ID",
        help=(
            "Required check. Needed when the default branch does not pin one to an app. "
            "Repeat for more than one."
        ),
    )
    onboard.add_argument(
        "--allowed-path",
        action="append",
        default=None,
        metavar="GLOB",
        help="Allowed path glob. Replaces the detected paths. Repeat to add another.",
    )
    onboard.add_argument(
        "--allowed-dependency",
        action="append",
        default=None,
        metavar="PKG",
        help="Replace allowed_dependency_changes. Repeat to add another package.",
    )
    onboard.add_argument(
        "--replace-funder",
        action="store_true",
        help="Allow replacing a different funder already set on the link.",
    )

    configure = sub.add_parser(
        "configure",
        parents=[write_flags],
        help="Set the funder, required checks, or policy allow-lists on an existing link.",
    )
    configure.add_argument("--repo", required=True, help="GitHub repo as OWNER/NAME")
    configure.add_argument("--funder-email", help="User who funds bounties on this repo.")
    configure.add_argument(
        "--check",
        action="append",
        default=None,
        type=parse_check,
        metavar="NAME:APP_ID",
        help="Replace required checks. Repeat for more than one. Split on the last colon.",
    )
    configure.add_argument(
        "--allowed-path",
        action="append",
        default=None,
        metavar="GLOB",
        help="Replace allowed_paths with these fnmatch globs. Repeat to add another glob.",
    )
    configure.add_argument(
        "--allowed-dependency",
        action="append",
        default=None,
        metavar="PKG",
        help="Replace allowed_dependency_changes. Repeat to add another package.",
    )

    enable = sub.add_parser(
        "enable",
        parents=[write_flags],
        help="Enable bounties. Requires a funder, a required check, and a live installation.",
    )
    enable.add_argument("--repo", required=True, help="GitHub repo as OWNER/NAME")

    disable = sub.add_parser("disable", parents=[write_flags], help="Disable bounties for a repo.")
    disable.add_argument("--repo", required=True, help="GitHub repo as OWNER/NAME")
    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


async def _invoke(db, args: argparse.Namespace) -> tuple[object, bool]:
    if args.command == "list":
        return await list_repo_links(db), False
    if args.command == "onboard":
        funder = await _require_user(db, args.funder_email)
        payload = await onboard_repo(
            db,
            args.repo,
            funder=funder,
            checks=args.check,
            allowed_paths=args.allowed_path,
            allowed_dependency_changes=args.allowed_dependency,
            replace_funder=args.replace_funder,
        )
        return payload, bool(payload["changed"])
    if args.command == "configure":
        payload = await configure_repo_link(
            db,
            args.repo,
            funder_email=args.funder_email,
            checks=args.check,
            allowed_paths=args.allowed_path,
            allowed_dependency_changes=args.allowed_dependency,
        )
        return payload, True
    if args.command == "enable":
        return await set_repo_enabled(db, args.repo, True), True
    if args.command == "disable":
        return await set_repo_enabled(db, args.repo, False), True
    raise RepoAdminError(f"unknown command {args.command}")


async def _dispatch(args: argparse.Namespace) -> int:
    async with async_session() as db:
        try:
            payload, writes = await _invoke(db, args)
        except RepoAdminError:
            await db.rollback()
            raise
        _print_json(payload)
        if not writes:
            return 0
        if args.apply:
            await db.commit()
            print("APPLIED")
        else:
            await db.rollback()
            print("DRY RUN (use --apply to write)")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except RepoAdminError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
