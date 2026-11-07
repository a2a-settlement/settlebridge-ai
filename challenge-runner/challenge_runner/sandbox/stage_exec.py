"""Execute stage inside the sandbox: offline install, then the test command.

TMPDIR is /work/tmp. The required /tmp mount is noexec, and pip executes build
scripts from TMPDIR.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def pip_install_argv(
    *,
    find_links: str,
    target: str,
    requirements: list[str],
    packages: list[str],
    wheels: list[str],
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        find_links,
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--target",
        target,
    ]
    for requirement in requirements:
        command.extend(["-r", requirement])
    command.extend(packages)
    command.extend(wheels)
    return command


def _env(target: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL", "PYTHONIOENCODING"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["HOME"] = "/tmp"
    env["PYTHONPATH"] = target
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PIP_NO_CACHE_DIR"] = "1"
    env["TMPDIR"] = "/work/tmp"
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="challenge_runner.sandbox.stage_exec")
    parser.add_argument("--find-links", default="/wheels")
    parser.add_argument("--target", default="/work/.pydeps")
    parser.add_argument("--requirement", action="append", default=[])
    parser.add_argument("--package", action="append", default=[])
    parser.add_argument("--all-wheels", action="store_true")
    parser.add_argument("test_command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    test_command = list(args.test_command)
    if test_command[:1] == ["--"]:
        test_command = test_command[1:]
    if not test_command:
        return 2
    Path("/work/tmp").mkdir(parents=True, exist_ok=True)
    Path(args.target).mkdir(parents=True, exist_ok=True)
    wheels: list[str] = []
    if args.all_wheels:
        wheels = sorted(str(path) for path in Path(args.find_links).glob("*.whl"))
    if args.requirement or args.package or wheels:
        install = pip_install_argv(
            find_links=args.find_links,
            target=args.target,
            requirements=list(args.requirement),
            packages=list(args.package),
            wheels=wheels,
        )
        completed = subprocess.run(
            install,
            shell=False,
            cwd="/work",
            env=_env(args.target),
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
    completed = subprocess.run(
        test_command,
        shell=False,
        cwd="/work",
        env=_env(args.target),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
