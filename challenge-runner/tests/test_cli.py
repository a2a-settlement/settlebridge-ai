"""CLI round trip. run_job and fetch_source are fakes; no docker and no network."""

from __future__ import annotations

import json
from pathlib import Path

from challenge_runner.__main__ import main
from challenge_runner.fetcher import FetchOutput
from challenge_runner.protocol import DIGEST_RE, RunnerResult
from challenge_runner.sandbox import SandboxUnavailable

DIGEST = "sha256:" + "ab" * 32


def _job(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "protocol": "challenge-runner/v1",
                "execution_id": "exec-0001ab",
                "stage": "execute",
                "task_id": "task-1",
                "source_tarball": DIGEST,
                "limits": {
                    "cpu_seconds": 30,
                    "memory_mb": 512,
                    "wall_seconds": 30,
                    "disk_mb": 1024,
                    "max_processes": 128,
                },
                "test_command": ["pytest", "-q"],
            }
        ),
        encoding="utf-8",
    )


def test_run_round_trip_writes_result_json(tmp_path: Path, monkeypatch):
    job = tmp_path / "job.json"
    out = tmp_path / "result.json"
    root = tmp_path / "artifacts"
    _job(job)
    seen = {}

    def fake_run_job(parsed, *, artifact_root):
        seen["execution_id"] = parsed.execution_id
        seen["root"] = artifact_root
        return RunnerResult(
            execution_id=parsed.execution_id,
            stage=parsed.stage,
            status="ok",
            image_digest=DIGEST,
            source_digest=parsed.source_tarball,
            exit_code=0,
            wall_seconds=2,
        )

    monkeypatch.setattr("challenge_runner.sandbox.run_job", fake_run_job)
    code = main(["run", "--job", str(job), "--out", str(out), "--artifact-root", str(root)])
    assert code == 0
    assert seen == {"execution_id": "exec-0001ab", "root": root}
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["status"] == "ok"
    assert body["protocol"] == "challenge-runner/v1"
    assert body["source_digest"] == DIGEST
    assert body["exit_code"] == 0


def test_run_maps_sandbox_unavailable_to_result(tmp_path: Path, monkeypatch):
    job = tmp_path / "job.json"
    out = tmp_path / "result.json"
    _job(job)

    def fake_run_job(_parsed, *, artifact_root):
        raise SandboxUnavailable("sandbox runtime unavailable")

    monkeypatch.setattr("challenge_runner.sandbox.run_job", fake_run_job)
    code = main(
        ["run", "--job", str(job), "--out", str(out), "--artifact-root", str(tmp_path / "art")]
    )
    assert code == 0
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["status"] == "sandbox_error"
    assert body["error"] == "sandbox runtime unavailable"
    assert DIGEST_RE.fullmatch(body["image_digest"])
    assert body["source_digest"] == DIGEST


def test_fetch_prints_digests_and_paths_only(tmp_path: Path, monkeypatch, capsys):
    token = "ghs_thismustnotprint1234"

    def fake_fetch(**kwargs):
        assert kwargs["token"] == token
        assert kwargs["repo_full_name"] == "acme/widgets"
        assert kwargs["sha"] == "a" * 40
        assert kwargs["include_history"] is True
        assert kwargs["workdir"] == tmp_path
        return FetchOutput(
            source_tarball=tmp_path / "source.tar",
            source_digest=DIGEST,
            history_bundle=tmp_path / "history.bundle",
            history_digest="sha256:" + "cd" * 32,
        )

    monkeypatch.setattr("challenge_runner.fetcher.fetch_source", fake_fetch)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(token + "\n"))
    code = main(
        [
            "fetch",
            "--repo",
            "acme/widgets",
            "--sha",
            "a" * 40,
            "--workdir",
            str(tmp_path),
            "--history",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err
    body = json.loads(captured.out)
    assert body["source_digest"] == DIGEST
    assert body["source_tarball"] == str(tmp_path / "source.tar")
    assert body["history_digest"] == "sha256:" + "cd" * 32
    assert body["history_bundle"] == str(tmp_path / "history.bundle")
    assert "token" not in body


def test_fetch_history_defaults_off(tmp_path: Path, monkeypatch, capsys):
    def fake_fetch(**kwargs):
        assert kwargs["include_history"] is False
        assert kwargs["token"] == "sbl_stdinsecret1234"
        return FetchOutput(
            source_tarball=tmp_path / "source.tar",
            source_digest=DIGEST,
            history_bundle=None,
            history_digest=None,
        )

    monkeypatch.setattr("challenge_runner.fetcher.fetch_source", fake_fetch)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("sbl_stdinsecret1234\n"))
    code = main(["fetch", "--repo", "acme/widgets", "--sha", "b" * 40, "--workdir", str(tmp_path)])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["history_bundle"] is None
    assert body["history_digest"] is None
    assert "sbl_stdinsecret1234" not in json.dumps(body)


def test_fetch_puts_the_tarball_in_the_store(tmp_path: Path, monkeypatch, capsys):
    import hashlib

    data = b"tarball-bytes"
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    work = tmp_path / "work"
    work.mkdir()
    (work / "source.tar.gz").write_bytes(data)

    def fake_fetch(**_kwargs):
        return FetchOutput(
            source_tarball=work / "source.tar.gz",
            source_digest=digest,
            history_bundle=None,
            history_digest=None,
        )

    monkeypatch.setattr("challenge_runner.fetcher.fetch_source", fake_fetch)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("sbl_stdinsecret1234\n"))
    root = tmp_path / "store"
    argv = ["fetch", "--repo", "acme/widgets", "--sha", "c" * 40, "--workdir", str(work)]
    assert main([*argv, "--artifact-root", str(root)]) == 0
    body = json.loads(capsys.readouterr().out)
    stored = Path(body["source_tarball"])
    assert stored == root / "sha256" / digest.split(":", 1)[1]
    assert stored.read_bytes() == data
    assert body["source_digest"] == digest
