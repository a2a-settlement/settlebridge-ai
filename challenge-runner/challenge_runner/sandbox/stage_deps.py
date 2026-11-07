"""Dependency stage inside the sandbox: pip download, then pip wheel.

TMPDIR is /out/tmp. The required /tmp mount is noexec, and pip executes build
scripts from TMPDIR.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def build_pip_invocations(
    *,
    dest: str,
    requirements: list[str],
    packages: list[str],
    require_hashes: bool,
) -> list[list[str]]:
    download = [sys.executable, "-m", "pip", "download", "--dest", dest, "--no-cache-dir"]
    wheel = [sys.executable, "-m", "pip", "wheel", "--wheel-dir", dest, "--no-cache-dir"]
    if require_hashes:
        download.append("--require-hashes")
        wheel.append("--require-hashes")
    for requirement in requirements:
        download.extend(["-r", requirement])
        wheel.extend(["-r", requirement])
    download.extend(packages)
    wheel.extend(packages)
    return [download, wheel]


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "HOME"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["PIP_NO_CACHE_DIR"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = "/out/tmp"
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="challenge_runner.sandbox.stage_deps")
    parser.add_argument("--dest", default="/out/wheels")
    parser.add_argument("--requirement", action="append", default=[])
    parser.add_argument("--package", action="append", default=[])
    parser.add_argument("--require-hashes", action="store_true")
    args = parser.parse_args(argv)
    Path("/out/tmp").mkdir(parents=True, exist_ok=True)
    Path(args.dest).mkdir(parents=True, exist_ok=True)
    if not args.requirement and not args.package:
        return 0
    env = _env()
    for command in build_pip_invocations(
        dest=args.dest,
        requirements=list(args.requirement),
        packages=list(args.package),
        require_hashes=args.require_hashes,
    ):
        completed = subprocess.run(command, shell=False, env=env, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
