"""Read the one compatibility manifest and export build settings from it."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from assurance.preflight import PreflightError, load_lock


def compose_env(
    lock_path: Path, *, settlement_context: Path, settlebridge_context: Path
) -> str:
    lock = load_lock(lock_path)
    compose_dir = lock_path.resolve().parent
    lines = [
        f"COMPOSE_DIR={compose_dir}",
        f"SETTLEMENT_CONTEXT={settlement_context.resolve()}",
        f"SETTLEBRIDGE_CONTEXT={settlebridge_context.resolve()}",
    ]
    for name, pin in lock["revisions"].items():
        key = name.upper().replace("-", "_") + "_SHA"
        lines.append(f"{key}={pin}")
    return "\n".join(lines) + "\n"


def check_working_tree(
    lock_path: Path, *, settlement_context: Path, settlebridge_context: Path
) -> list[str]:
    lock = load_lock(lock_path)
    roots = {
        "a2a-settlement": settlement_context,
        "settlebridge-ai": settlebridge_context,
    }
    notes = []
    for repo, paths in lock.get("required_paths", {}).items():
        root = roots.get(repo)
        if root is None:
            continue
        for rel in paths:
            if not (root / rel).is_file():
                raise PreflightError(f"{repo} checkout is missing {rel}")
        notes.append(f"{repo} checkout contains {len(paths)} required paths")
    return notes


def check_published_pins(
    lock_path: Path, *, settlement_context: Path, settlebridge_context: Path
) -> list[str]:
    """Fail when the locked revision does not contain the implementation."""
    import subprocess

    lock = load_lock(lock_path)
    roots = {
        "a2a-settlement": settlement_context,
        "settlebridge-ai": settlebridge_context,
    }
    notes = []
    for repo, paths in lock.get("required_paths", {}).items():
        root = roots.get(repo)
        pin = lock["revisions"].get(repo, "")
        if root is None or not (root / ".git").exists():
            raise PreflightError(f"{repo} has no git checkout to verify pin {pin}")
        for rel in paths:
            proc = subprocess.run(
                ["git", "cat-file", "-e", f"{pin}:{rel}"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise PreflightError(
                    f"{repo} pin {pin} does not contain {rel}; commit the implementation and move the pin"
                )
        notes.append(f"{repo} pin {pin} contains the required paths")
    return notes


def check_image_ids(lock_path: Path) -> list[str]:
    lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    images = lock.get("images") or {}
    missing = [
        name for name, digest in images.items() if not str(digest).startswith("sha256:")
    ]
    if missing:
        raise PreflightError(
            "image digests are not recorded for: " + ", ".join(missing)
        )
    return [f"images={len(images)}"]


def service_urls_from_env(env: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if env is None else env
    return {
        "EXCHANGE_URL": env.get("EXCHANGE_URL", ""),
        "GATEWAY_URL": env.get("GATEWAY_URL", ""),
        "BOARD_URL": env.get("BOARD_URL", ""),
        "OBSERVER_URL": env.get("OBSERVER_URL", ""),
    }
