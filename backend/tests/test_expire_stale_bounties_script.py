"""scripts/expire_stale_bounties.py: which exchange key it uses, and how it reads a refusal."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "expire_stale_bounties.py"


@pytest.fixture
def script(monkeypatch):
    for name in ("EXCHANGE_OPERATOR_API_KEY", "A2A_OPERATOR_API_KEY", "EXCHANGE_CREDENTIALS_FILE"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("expire_stale_bounties", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operator_key_from_environment_wins(script, monkeypatch, tmp_path):
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"requester": {"api_key": "ate_requester"}}))
    monkeypatch.setenv("EXCHANGE_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("EXCHANGE_OPERATOR_API_KEY", "ate_operator")
    assert script.load_exchange_key() == ("ate_operator", "operator")


def test_alternate_operator_variable_is_read(script, monkeypatch):
    monkeypatch.setenv("A2A_OPERATOR_API_KEY", "ate_operator")
    assert script.load_exchange_key() == ("ate_operator", "operator")


def test_credentials_file_prefers_operator_then_requester(script, monkeypatch, tmp_path):
    creds = tmp_path / "creds.json"
    monkeypatch.setenv("EXCHANGE_CREDENTIALS_FILE", str(creds))
    creds.write_text(json.dumps({"requester": {"api_key": "ate_requester"}}))
    assert script.load_exchange_key() == ("ate_requester", "requester")
    creds.write_text(
        json.dumps({"requester": {"api_key": "ate_r"}, "operator": {"api_key": "ate_o"}})
    )
    assert script.load_exchange_key() == ("ate_o", "operator")


def test_no_key_configured_stops(script):
    with pytest.raises(SystemExit):
        script.load_exchange_key()


@pytest.mark.parametrize(
    ("code", "expected"),
    [(403, "forbidden"), (404, "not_found"), (500, "error:HTTP 500")],
)
def test_http_errors_are_named(script, monkeypatch, code, expected):
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "x", None, io.BytesIO(b"{}"))

    monkeypatch.setattr(script.urllib.request, "urlopen", refuse)
    assert script.escrow_status("ate_key", "escrow-1") == expected


def test_a_refused_escrow_is_never_treated_as_terminal(script):
    assert "forbidden" not in script.TERMINAL
