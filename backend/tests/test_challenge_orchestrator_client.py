"""Runner client: job JSON in a file, fetch token on stdin only."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.schemas.challenge import ResourceLimits, RunnerJob
from app.services.challenge_runner_client import (
    RunnerInvocationError,
    fetch_source,
    run_runner,
)

SECRET = "ghs_SUPERSECRET"
DIGEST = "sha256:" + ("ab" * 32)
IMAGE = "sha256:" + ("cd" * 32)


def _job() -> RunnerJob:
    return RunnerJob(
        execution_id="exec-client-1",
        stage="execute",
        task_id="task-1",
        source_tarball=DIGEST,
        limits=ResourceLimits(cpu_seconds=30, memory_mb=256, wall_seconds=30, disk_mb=256),
        test_command=["pytest", "-q"],
    )


def _result(execution_id: str = "exec-client-1") -> dict:
    return {
        "protocol": "challenge-runner/v1",
        "execution_id": execution_id,
        "stage": "execute",
        "status": "ok",
        "exit_code": 0,
        "cpu_seconds": 1,
        "wall_seconds": 1,
        "image_digest": IMAGE,
        "source_digest": DIGEST,
        "observer_events": [],
        "log_digest": "sha256:" + ("ef" * 32),
        "error": None,
        "wheelhouse_manifest": None,
    }


def test_run_writes_a_job_file_and_parses_the_result(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    captured: dict = {}

    def fake_run(cmd, input=None, env=None, **_kwargs):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["env"] = env
        job_path = Path(cmd[cmd.index("--job") + 1])
        out_path = Path(cmd[cmd.index("--out") + 1])
        captured["job"] = job_path.read_text(encoding="utf-8")
        out_path.write_text(json.dumps(_result()), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", f"stderr {SECRET}")

    monkeypatch.setattr("app.services.challenge_runner_client.subprocess.run", fake_run)
    result = run_runner(_job(), artifact_root=str(tmp_path))
    assert result.status == "ok"
    assert result.execution_id == "exec-client-1"
    assert captured["cmd"][1:4] == ["-m", "challenge_runner", "run"]
    assert SECRET not in captured["job"]
    assert SECRET not in " ".join(captured["cmd"])
    assert captured["input"] is None
    assert "GITHUB_TOKEN" not in captured["env"]
    assert SECRET not in json.dumps(captured["env"])
    assert "token" not in json.loads(captured["job"])


def test_run_rejects_a_token_hidden_in_the_job(tmp_path):
    job = _job().model_copy(update={"install_command": ["pip", "install", SECRET]})
    with pytest.raises(RunnerInvocationError) as caught:
        run_runner(job, artifact_root=str(tmp_path))
    assert SECRET not in str(caught.value)


def test_fetch_passes_the_token_only_on_stdin(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", SECRET)
    captured: dict = {}

    def fake_run(cmd, input=None, env=None, **_kwargs):
        captured["cmd"] = list(cmd)
        captured["input"] = input
        captured["env"] = env
        stdout = json.dumps(
            {
                "source_tarball": str(tmp_path / "src.tar"),
                "source_digest": DIGEST,
                "history_bundle": None,
                "history_digest": None,
            }
        )
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr("app.services.challenge_runner_client.subprocess.run", fake_run)
    fetched = fetch_source(
        repo_full_name="acme/app",
        sha="a" * 40,
        token=SECRET,
        workdir=str(tmp_path),
    )
    assert fetched.source_digest == DIGEST
    assert captured["cmd"][1:4] == ["-m", "challenge_runner", "fetch"]
    assert captured["input"] == SECRET + "\n"
    assert SECRET not in " ".join(captured["cmd"])
    assert "GH_TOKEN" not in captured["env"]
    assert SECRET not in json.dumps(captured["env"])


def test_runner_failure_does_not_surface_stderr(monkeypatch, tmp_path):
    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", f"blew up {SECRET}")

    monkeypatch.setattr("app.services.challenge_runner_client.subprocess.run", fake_run)
    with pytest.raises(RunnerInvocationError) as caught:
        run_runner(_job(), artifact_root=str(tmp_path))
    assert SECRET not in str(caught.value)
    assert "blew up" not in str(caught.value)
