"""CLI for challenge-runner/v1.

``run`` writes a RunnerResult. SandboxUnavailable becomes status sandbox_error
and exit 0. ``fetch`` reads the token from one stdin line and prints only
digests and paths.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from challenge_runner.protocol import RunnerJob


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="challenge_runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--job", required=True)
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--artifact-root", required=True)

    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("--repo", required=True)
    fetch_parser.add_argument("--sha", required=True)
    fetch_parser.add_argument("--workdir", required=True)
    fetch_parser.add_argument("--history", action="store_true")
    fetch_parser.add_argument("--artifact-root")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    return _cmd_fetch(args)


def _cmd_run(args: argparse.Namespace) -> int:
    from challenge_runner.sandbox import SandboxUnavailable, run_job
    from challenge_runner.sandbox.runner import unavailable_result

    job = RunnerJob.from_dict(json.loads(Path(args.job).read_text(encoding="utf-8")))
    try:
        result = run_job(job, artifact_root=Path(args.artifact_root))
    except SandboxUnavailable as exc:
        result = unavailable_result(job, str(exc))
    Path(args.out).write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    from challenge_runner.fetcher import fetch_source

    token = sys.stdin.readline()
    if token.endswith("\n"):
        token = token[:-1]
    output = fetch_source(
        repo_full_name=args.repo,
        sha=args.sha,
        token=token,
        workdir=Path(args.workdir),
        include_history=bool(args.history),
    )
    source_tarball = output.source_tarball
    history_bundle = output.history_bundle
    if args.artifact_root:
        # Sandbox stages read source by digest from the store, not from the workdir.
        source_tarball = _store_copy(args.artifact_root, source_tarball, output.source_digest)
        if history_bundle is not None and output.history_digest:
            history_bundle = _store_copy(args.artifact_root, history_bundle, output.history_digest)
    payload = {
        "source_tarball": str(source_tarball),
        "source_digest": output.source_digest,
        "history_bundle": None if history_bundle is None else str(history_bundle),
        "history_digest": output.history_digest,
    }
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    return 0


def _store_copy(artifact_root: str, path: Path, expected: str) -> Path:
    from challenge_runner.artifacts import ArtifactStore

    store = ArtifactStore(Path(artifact_root))
    digest = store.put_file(Path(path))
    if digest != expected:
        raise SystemExit("fetched bytes do not match their digest")
    return store.get_path(digest)


if __name__ == "__main__":
    sys.exit(main())
