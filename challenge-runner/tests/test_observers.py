"""Verifier observers fire only on their own capture records."""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from challenge_runner.observers import OBSERVER_NAMES, get_observer
from challenge_runner.observers._io import MAX_BYTES
from challenge_runner.protocol import ObserverEvent

TOKEN_A = "TENANT-A-CANARY-TOKEN-0001"
TOKEN_B = "TENANT-B-CANARY-TOKEN-0002"


def _write(directory: Path, name: str, rows: list[dict]) -> None:
    payload = "".join(json.dumps(row) + "\n" for row in rows)
    (directory / name).write_text(payload, encoding="utf-8")


def _events(name: str, directory: Path) -> list[ObserverEvent]:
    found = list(get_observer(name).observe(str(directory)))
    assert all(isinstance(event, ObserverEvent) for event in found)
    assert all(event.observer == name for event in found)
    return found


def test_registry_returns_each_observer_and_rejects_unknown_names():
    for name in OBSERVER_NAMES:
        assert get_observer(name).name == name
        assert list(get_observer(name).observe("/no/such/capture")) == []
    with pytest.raises(NotImplementedError):
        get_observer("authorization_canary")
    with pytest.raises(NotImplementedError):
        get_observer(None)  # type: ignore[arg-type]


def test_imports_stay_inside_stdlib_and_the_runner_protocol():
    root = Path(__file__).resolve().parents[1] / "challenge_runner" / "observers"
    allowed_prefix = ("challenge_runner.protocol", "challenge_runner.observers")
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                top = module.split(".", 1)[0]
                if top == "challenge_runner":
                    assert module.startswith(allowed_prefix), module
                else:
                    assert top in sys.stdlib_module_names, module


def test_authorization_fires_only_for_a_tenant_b_canary_seen_by_tenant_a(tmp_path: Path):
    _write(
        tmp_path,
        "canaries.jsonl",
        [
            {"token": TOKEN_A, "owner": "tenant-a"},
            {"token": TOKEN_B, "owner": "Tenant_B"},
        ],
    )
    _write(
        tmp_path,
        "authorization.jsonl",
        [
            {
                "identity": "tenant-a",
                "channel": "response",
                "body": f"own {TOKEN_A}",
                "location": "GET /a",
            },
            {
                "identity": "tenant-a",
                "channel": "response",
                "body": f"leaked {TOKEN_B}",
                "location": "GET /items",
            },
            {
                "identity": "tenant-b",
                "channel": "response",
                "body": f"own {TOKEN_B}",
                "location": "GET /b",
            },
            {
                "identity": "Tenant-A",
                "channel": "log",
                "body": f"leaked {TOKEN_B}",
                "location": "app.log",
            },
            {
                "identity": "tenant_a",
                "channel": "side-channel",
                "body": f"also {TOKEN_B}",
                "location": "trace",
            },
        ],
    )
    found = _events("authorization_confidentiality", tmp_path)
    assert [(event.kind, event.location, event.detail) for event in found] == [
        ("canary_seen", "GET /items", "tenant-b canary in tenant-a response"),
        ("canary_seen", "app.log", "tenant-b canary in tenant-a log"),
        ("canary_seen", "trace", "tenant-b canary in tenant-a side_channel"),
    ]
    assert TOKEN_B not in found[0].detail
    assert TOKEN_A not in found[0].detail


def test_authorization_ignores_tenant_a_data_short_tokens_and_bad_channels(tmp_path: Path):
    _write(
        tmp_path,
        "canaries.jsonl",
        [
            {"token": TOKEN_A, "owner": "tenant-a"},
            {"token": "short-b", "owner": "tenant-b"},
            {"token": TOKEN_B, "owner": "tenant-a"},
        ],
    )
    # TOKEN_B is also owned by tenant-a, so it is ambiguous and must not fire.
    _write(
        tmp_path,
        "authorization.jsonl",
        [
            {"identity": "tenant-a", "channel": "response", "body": TOKEN_A, "location": "GET /a"},
            {
                "identity": "tenant-a",
                "channel": "response",
                "body": "short-b",
                "location": "GET /a",
            },
            {"identity": "tenant-a", "channel": "response", "body": TOKEN_B, "location": "GET /a"},
            {"identity": "tenant-a", "channel": "file", "body": TOKEN_B, "location": "x"},
        ],
    )
    assert _events("authorization_confidentiality", tmp_path) == []


def test_integrity_fires_only_for_an_unauthorized_protected_change(tmp_path: Path):
    _write(
        tmp_path,
        "integrity.jsonl",
        [
            _snap("before", "records", "b-1", "aaa", authorized=False),
            _snap("after", "records", "b-1", "bbb", authorized=False),
            _snap("before", "records", "own", "aaa", authorized=True),
            _snap("after", "records", "own", "bbb", authorized=True),
            _snap("before", "records", "open", "aaa", authorized=False, protected=False),
            _snap("after", "records", "open", "ccc", authorized=False, protected=False),
            _snap("before", "records", "same", "ddd", authorized=False),
            _snap("after", "records", "same", "ddd", authorized=False),
        ],
    )
    found = _events("integrity", tmp_path)
    assert len(found) == 1
    assert found[0].kind == "state_changed"
    assert found[0].location == "records/b-1"
    assert found[0].detail == "protected row changed under an unauthorized identity"


def test_integrity_state_objects_ignore_key_order_and_ambiguous_phases(tmp_path: Path):
    _write(
        tmp_path,
        "integrity.jsonl",
        [
            {
                "phase": "before",
                "identity": "tenant-a",
                "authorized": False,
                "table": "t",
                "row_id": "same",
                "protected": True,
                "state": {"b": 1, "a": 2},
            },
            {
                "phase": "after",
                "identity": "tenant-a",
                "authorized": False,
                "table": "t",
                "row_id": "same",
                "protected": True,
                "state": {"a": 2, "b": 1},
            },
            {
                "phase": "before",
                "identity": "tenant-a",
                "authorized": False,
                "table": "t",
                "row_id": "changed",
                "protected": True,
                "state": {"n": 1},
            },
            {
                "phase": "after",
                "identity": "tenant-a",
                "authorized": False,
                "table": "t",
                "row_id": "changed",
                "protected": True,
                "state": {"n": 2},
            },
            {
                "phase": "before",
                "identity": "tenant-a",
                "authorized": False,
                "table": "t",
                "row_id": "dup",
                "protected": True,
                "digest": "one",
            },
            {
                "phase": "before",
                "identity": "tenant-a",
                "authorized": False,
                "table": "t",
                "row_id": "dup",
                "protected": True,
                "digest": "two",
            },
            {
                "phase": "after",
                "identity": "tenant-a",
                "authorized": False,
                "table": "t",
                "row_id": "dup",
                "protected": True,
                "digest": "three",
            },
        ],
    )
    found = _events("integrity", tmp_path)
    assert [event.location for event in found] == ["t/changed"]


def test_injection_fires_for_file_and_command_sentinels_only(tmp_path: Path):
    _write(
        tmp_path,
        "injection.jsonl",
        [
            {"sentinel": "file", "triggered": True, "location": "/verifier/sentinel/file"},
            {"sentinel": "command", "triggered": True, "location": "/verifier/sentinel/command"},
            {"sentinel": "file", "triggered": False, "location": "/verifier/sentinel/quiet"},
            {"sentinel": "file", "triggered": "true", "location": "/verifier/sentinel/string"},
            {"sentinel": "other", "triggered": True, "location": "/tmp/nope"},
        ],
    )
    found = _events("injection_execution", tmp_path)
    assert [event.location for event in found] == [
        "/verifier/sentinel/file",
        "/verifier/sentinel/command",
    ]
    assert {event.kind for event in found} == {"sentinel_triggered"}


def test_denial_of_service_fires_only_above_the_contract_threshold(tmp_path: Path):
    _write(
        tmp_path,
        "resources.jsonl",
        [
            {"metric": "cpu_seconds", "value": 10, "threshold": 10},
            {"metric": "wall_seconds", "value": 31, "threshold": 30},
            {"metric": "memory_mb", "value": True, "threshold": 1},
            {"metric": "memory_mb", "value": 64, "threshold": 256},
            {"metric": "disk_mb", "value": float("inf"), "threshold": 1},
            {"metric": "mystery", "value": 99, "threshold": 1},
            {"metric": "max_processes", "value": 9, "threshold": 8},
        ],
    )
    found = _events("denial_of_service", tmp_path)
    assert [(event.kind, event.location) for event in found] == [
        ("threshold_exceeded", "wall_seconds"),
        ("threshold_exceeded", "max_processes"),
    ]
    assert "threshold" in found[0].detail


def test_regression_reports_a_failed_trusted_fixture_test(tmp_path: Path):
    _write(
        tmp_path,
        "regression.jsonl",
        [
            {"test_id": "tests/test_accept.py::test_ok", "outcome": "passed", "trusted": True},
            {"test_id": "tests/test_accept.py::test_paid", "outcome": "failed", "trusted": True},
            {
                "test_id": "challenge_tests/test_claim.py::test_x",
                "outcome": "failed",
                "trusted": True,
            },
            {
                "test_id": "tests/test_accept.py::test_untrusted",
                "outcome": "failed",
                "trusted": False,
            },
        ],
    )
    found = _events("regression", tmp_path)
    assert len(found) == 1
    assert found[0].kind == "state_changed"
    assert found[0].location == "tests/test_accept.py::test_paid"
    assert found[0].detail == "trusted acceptance test failed"


@pytest.mark.parametrize("name", OBSERVER_NAMES)
def test_each_observer_has_a_positive_and_a_negative_capture(name, tmp_path: Path):
    negative = tmp_path / "negative"
    positive = tmp_path / "positive"
    negative.mkdir()
    positive.mkdir()
    _seed(negative, name, fire=False)
    _seed(positive, name, fire=True)
    assert _events(name, negative) == []
    found = _events(name, positive)
    assert found
    assert found[0].kind in {
        "canary_seen",
        "state_changed",
        "sentinel_triggered",
        "threshold_exceeded",
    }


@pytest.mark.parametrize("name", OBSERVER_NAMES)
def test_malformed_oversized_and_missing_captures_do_not_fire(name, tmp_path: Path):
    assert list(get_observer(name).observe(str(tmp_path))) == []
    assert list(get_observer(name).observe(None)) == []  # type: ignore[arg-type]
    garbage = tmp_path / _filename(name)
    garbage.write_bytes(b'\xff\xfe not json\n{"triggered": true}\n')
    assert _events(name, tmp_path) == []
    garbage.unlink()
    _seed(tmp_path, name, fire=True)
    target = tmp_path / _filename(name)
    good = target.read_bytes()
    target.write_text("not-json\n" + good.decode("utf-8"), encoding="utf-8")
    assert _events(name, tmp_path) == []
    target.write_bytes(b"x" * (MAX_BYTES + 1))
    assert _events(name, tmp_path) == []


def test_symlink_and_non_object_json_are_not_captures(tmp_path: Path):
    real = tmp_path / "real.jsonl"
    real.write_text(
        json.dumps(
            {"identity": "tenant-a", "channel": "response", "body": TOKEN_B, "location": "GET /a"}
        )
        + "\n",
        encoding="utf-8",
    )
    os.symlink(real, tmp_path / "authorization.jsonl")
    _write(tmp_path, "canaries.jsonl", [{"token": TOKEN_B, "owner": "tenant-b"}])
    assert _events("authorization_confidentiality", tmp_path) == []
    (tmp_path / "authorization.jsonl").unlink()
    (tmp_path / "authorization.jsonl").write_text(
        json.dumps(["not", "an", "object"]) + "\n", encoding="utf-8"
    )
    assert _events("authorization_confidentiality", tmp_path) == []


def _filename(name: str) -> str:
    return {
        "authorization_confidentiality": "authorization.jsonl",
        "integrity": "integrity.jsonl",
        "injection_execution": "injection.jsonl",
        "denial_of_service": "resources.jsonl",
        "regression": "regression.jsonl",
    }[name]


def _snap(phase, table, row_id, digest, *, authorized, protected=True):
    return {
        "phase": phase,
        "identity": "tenant-a",
        "authorized": authorized,
        "table": table,
        "row_id": row_id,
        "protected": protected,
        "digest": digest,
    }


def _seed(directory: Path, name: str, *, fire: bool) -> None:
    if name == "authorization_confidentiality":
        _write(
            directory,
            "canaries.jsonl",
            [{"token": TOKEN_B, "owner": "tenant-b"}, {"token": TOKEN_A, "owner": "tenant-a"}],
        )
        body = TOKEN_B if fire else TOKEN_A
        _write(
            directory,
            "authorization.jsonl",
            [
                {
                    "identity": "tenant-a",
                    "channel": "response",
                    "body": body,
                    "location": "GET /items",
                }
            ],
        )
    elif name == "integrity":
        after = "bbb" if fire else "aaa"
        authorized = False if fire else True
        _write(
            directory,
            "integrity.jsonl",
            [
                _snap("before", "records", "b-1", "aaa", authorized=False),
                _snap("after", "records", "b-1", after, authorized=authorized),
            ],
        )
    elif name == "injection_execution":
        _write(
            directory,
            "injection.jsonl",
            [{"sentinel": "command", "triggered": fire, "location": "/verifier/sentinel/command"}],
        )
    elif name == "denial_of_service":
        value = 11 if fire else 9
        _write(
            directory,
            "resources.jsonl",
            [{"metric": "cpu_seconds", "value": value, "threshold": 10}],
        )
    else:
        outcome = "failed" if fire else "passed"
        _write(
            directory,
            "regression.jsonl",
            [{"test_id": "tests/test_accept.py::test_ok", "outcome": outcome, "trusted": True}],
        )
