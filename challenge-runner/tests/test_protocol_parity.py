"""The stdlib protocol accepts exactly the shapes the shared JSON Schema describes."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import jsonschema
import pytest

from challenge_runner.protocol import (
    ObserverEvent,
    ProtocolError,
    ResourceLimits,
    RunnerJob,
    RunnerResult,
)

_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2] / "docs" / "cyber" / "runner-protocol.schema.json"
    ).read_text()
)
D = "sha256:" + "a" * 64


def _job() -> dict:
    return {
        "protocol": "challenge-runner/v1",
        "execution_id": "exec-0001",
        "stage": "execute",
        "task_id": "t1",
        "source_tarball": D,
        "wheelhouse_manifest": D,
        "fixtures_digest": D,
        "challenger_bundle": D,
        "python_version": "3.11",
        "install_command": ["pip", "install", "--no-index", "-r", "requirements.txt"],
        "test_command": ["pytest", "-q", "challenge_tests"],
        "allowlist_hosts": ["pypi.org", "files.pythonhosted.org"],
        "limits": {
            "cpu_seconds": 600,
            "memory_mb": 2048,
            "wall_seconds": 900,
            "disk_mb": 4096,
            "max_processes": 256,
        },
        "observers": ["authorization_canary"],
        "canaries_digest": D,
    }


def test_field_names_match_schema():
    assert {f.name for f in fields(RunnerJob)} == set(_SCHEMA["job"]["properties"])
    assert {f.name for f in fields(RunnerResult)} == set(_SCHEMA["result"]["properties"])
    defs = _SCHEMA["job"]["$defs"]
    assert {f.name for f in fields(ResourceLimits)} == set(defs["ResourceLimits"]["properties"])
    result_defs = _SCHEMA["result"]["$defs"]
    assert {f.name for f in fields(ObserverEvent)} == set(
        result_defs["ObserverEvent"]["properties"]
    )


def test_sample_job_valid_on_both_sides_and_round_trips():
    data = _job()
    jsonschema.validate(data, _SCHEMA["job"])
    job = RunnerJob.from_dict(data)
    assert job.to_dict() == data


def test_unknown_keys_and_bad_digests_rejected():
    data = _job()
    data["token"] = "x"
    with pytest.raises(ProtocolError):
        RunnerJob.from_dict(data)
    data = _job()
    data["source_tarball"] = "sha256:short"
    with pytest.raises(ProtocolError):
        RunnerJob.from_dict(data)


def test_result_serializes_to_schema():
    result = RunnerResult(
        execution_id="exec-0001",
        stage="execute",
        status="test_failed",
        image_digest=D,
        source_digest=D,
        exit_code=1,
        observer_events=(
            ObserverEvent(
                observer="authorization_canary",
                kind="canary_seen",
                detail="tenant-B canary in tenant-A response",
                location="GET /api/v1/bounties/x",
            ),
        ),
    )
    jsonschema.validate(result.to_dict(), _SCHEMA["result"])
