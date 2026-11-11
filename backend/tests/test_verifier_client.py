"""Public verifier client: inconclusive unless the private package is enabled."""

from __future__ import annotations

import importlib.abc
import logging
import sys
import types

import pytest

from app.config import settings
from app.services import verifier_client
from app.services.verifier_client import classify_revert, verifier_available, verify

_MARKER = "request-marker-9f3c"


@pytest.fixture(autouse=True)
def _reset_import_cache():
    verifier_client._cached = False
    verifier_client._cached_module = None
    verifier_client._cached_error = None
    verifier_client._cached_token = verifier_client._UNSET
    yield
    verifier_client._cached = False
    verifier_client._cached_module = None
    verifier_client._cached_error = None
    verifier_client._cached_token = verifier_client._UNSET


def _request(**overrides) -> dict:
    advisory = {
        "vuln_id": "GHSA-xxxx-yyyy-zzzz",
        "aliases": ["CVE-2024-0001"],
        "summary": "example",
        "details": "details",
        "package": "example-pkg",
        "ecosystem": "pypi",
        "references": ["https://example.invalid/advisory"],
    }
    advisory.update(overrides.pop("advisory", {}))
    body = {
        "repo": "octo/example",
        "pr": 32,
        "base_commit": "a" * 40,
        "fix_commit": "b" * 40,
        "advisory": advisory,
        "solver_kind": "house",
        "solver_principal_sha256": "c" * 64,
        "budget_cents": 100,
        "seed": 1,
    }
    body.update(overrides)
    return body


def _drop_verifier_modules(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "settlebridge_verifier" or name.startswith("settlebridge_verifier."):
            monkeypatch.delitem(sys.modules, name, raising=False)


class _BlockVerifier(importlib.abc.MetaPathFinder):
    """Fail closed even when a real private package is installed in the env."""

    def find_spec(self, fullname, path, target=None):
        if fullname == "settlebridge_verifier" or fullname.startswith("settlebridge_verifier."):
            raise ModuleNotFoundError(fullname)
        return None


def _hide_verifier(monkeypatch) -> None:
    _drop_verifier_modules(monkeypatch)
    monkeypatch.setattr(sys, "meta_path", [_BlockVerifier(), *sys.meta_path])


def _install_verifier(monkeypatch, *, verify_fn=None, classify_fn=None) -> None:
    _drop_verifier_modules(monkeypatch)
    package = types.ModuleType("settlebridge_verifier")
    package.__path__ = []  # type: ignore[attr-defined]
    service = types.ModuleType("settlebridge_verifier.service")
    if verify_fn is not None:
        service.verify = verify_fn
    labels = types.ModuleType("settlebridge_verifier.labels")
    labels.__path__ = []  # type: ignore[attr-defined]
    revert = types.ModuleType("settlebridge_verifier.labels.revert")
    if classify_fn is not None:
        revert.classify_revert = classify_fn
    monkeypatch.setitem(sys.modules, "settlebridge_verifier", package)
    monkeypatch.setitem(sys.modules, "settlebridge_verifier.service", service)
    monkeypatch.setitem(sys.modules, "settlebridge_verifier.labels", labels)
    monkeypatch.setitem(sys.modules, "settlebridge_verifier.labels.revert", revert)


def test_flag_off_is_inconclusive_and_does_not_call(monkeypatch, caplog):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", False)
    called = False

    def verify_fn(_request):
        nonlocal called
        called = True
        return {"verdict": "verified"}

    _install_verifier(monkeypatch, verify_fn=verify_fn)
    with caplog.at_level(logging.ERROR, logger="app.services.verifier_client"):
        result = verify(_request())
        npm = _request()
        npm["advisory"]["ecosystem"] = "npm"
        outside = verify(npm)
    assert verifier_available() is False
    assert called is False
    assert caplog.records == []
    assert result == {
        "verdict": "inconclusive",
        "reason": "infra_error",
        "vex_status": "under_investigation",
        "detail": "FIX_VERIFIER_ENABLED is false",
    }
    assert outside["reason"] == "unsupported_project"


def test_missing_package_is_infra_error_inside_scope(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    _hide_verifier(monkeypatch)
    assert verifier_available() is False
    assert verify(_request()) == {
        "verdict": "inconclusive",
        "reason": "infra_error",
        "vex_status": "under_investigation",
        "detail": "settlebridge_verifier is not installed",
    }
    npm = _request()
    npm["advisory"]["ecosystem"] = "npm"
    assert verify(npm)["reason"] == "unsupported_project"


def test_package_exception_is_logged_without_the_request(monkeypatch, caplog):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    request = _request()
    request["advisory"]["summary"] = _MARKER

    def explode(payload):
        raise RuntimeError(f"{_MARKER} {payload['repo']} {payload['advisory']['summary']}")

    _install_verifier(monkeypatch, verify_fn=explode)
    with caplog.at_level(logging.DEBUG, logger="app.services.verifier_client"):
        result = verify(request)
    assert result["verdict"] == "inconclusive"
    assert result["reason"] == "infra_error"
    assert result["vex_status"] == "under_investigation"
    assert result["detail"] == "settlebridge_verifier.service.verify failed: RuntimeError"
    assert _MARKER not in result["detail"]
    assert request["repo"] not in result["detail"]
    assert _MARKER not in caplog.text
    assert request["repo"] not in caplog.text
    assert "RuntimeError" in caplog.text


def test_verify_returns_the_private_result(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    seen = {}

    def verify_fn(request):
        seen["request"] = request
        return {"verdict": "verified", "reason": "controls_passed", "vex_status": "fixed"}

    _install_verifier(monkeypatch, verify_fn=verify_fn)
    request = _request(solver_kind="third_party", seed=7)
    assert verifier_available() is True
    assert verify(request) == {
        "verdict": "verified",
        "reason": "controls_passed",
        "vex_status": "fixed",
    }
    assert seen["request"] is request
    assert seen["request"]["solver_kind"] == "third_party"
    assert seen["request"]["seed"] == 7
    assert seen["request"]["advisory"]["ecosystem"] == "pypi"


def test_pypi_ecosystem_match_ignores_case_and_whitespace(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    calls = []

    def verify_fn(request):
        calls.append(request["advisory"]["ecosystem"])
        return {"verdict": "verified"}

    _install_verifier(monkeypatch, verify_fn=verify_fn)
    request = _request()
    request["advisory"]["ecosystem"] = "  PyPI  "
    assert verify(request) == {"verdict": "verified"}
    assert calls == ["  PyPI  "]


def test_private_non_dict_result_is_infra_error(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    _install_verifier(monkeypatch, verify_fn=lambda _request: "verified")
    result = verify(_request())
    assert result["reason"] == "infra_error"
    assert result["verdict"] == "inconclusive"
    assert "non-dict" in result["detail"]


def test_non_pypi_does_not_call_when_it_is_the_first_request(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    calls = []

    def verify_fn(request):
        calls.append(request["advisory"]["ecosystem"])
        return {"verdict": "verified"}

    _install_verifier(monkeypatch, verify_fn=verify_fn)
    request = _request()
    request["advisory"]["ecosystem"] = "npm"
    assert verify(request)["reason"] == "unsupported_project"
    assert calls == []
    request["advisory"] = "not-an-advisory"
    assert verify(request)["reason"] == "unsupported_project"
    assert calls == []


def test_classify_revert_falls_back_to_unknown(monkeypatch, caplog):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", False)

    def classify_fn(_message, _linked):
        return "true"

    _install_verifier(monkeypatch, classify_fn=classify_fn)
    assert classify_revert("revert the security fix", ["CVE-2024-0001"]) == "unknown"

    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    _hide_verifier(monkeypatch)
    assert classify_revert("revert the security fix", ["CVE-2024-0001"]) == "unknown"

    def explode(message, _linked):
        raise RuntimeError(message)

    _install_verifier(monkeypatch, classify_fn=explode)
    with caplog.at_level(logging.DEBUG, logger="app.services.verifier_client"):
        result = classify_revert(_MARKER, ["security"])
    assert result == "unknown"
    assert result != "true"
    assert _MARKER not in caplog.text


def test_classify_revert_delegates_to_the_shared_rule(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    seen = {}

    def classify_fn(message, linked_texts):
        seen["message"] = message
        seen["linked"] = linked_texts
        return "true"

    _install_verifier(monkeypatch, classify_fn=classify_fn)
    linked = ["CVE-2024-0001", "fixes the auth bypass"]
    assert classify_revert("this reverts a security fix", linked) == "true"
    assert seen["message"] == "this reverts a security fix"
    assert seen["linked"] is linked


def test_classify_revert_rejects_values_outside_the_rule(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    for value in ("yes", "TRUE", 1, None):
        _install_verifier(monkeypatch, classify_fn=lambda _m, _l, v=value: v)
        assert classify_revert("revert", []) == "unknown"


def test_import_cache_invalidates_when_the_package_appears(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", True)
    _hide_verifier(monkeypatch)
    assert verifier_available() is False
    assert verify(_request())["reason"] == "infra_error"

    def verify_fn(_request):
        return {"verdict": "verified"}

    _install_verifier(monkeypatch, verify_fn=verify_fn)
    assert verifier_available() is True
    assert verify(_request()) == {"verdict": "verified"}
