"""OpenAI-compatible challenger client. urllib is mocked; no network."""

from __future__ import annotations

import io
import json
import traceback
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from challenge_runner.challenger import http_client
from challenge_runner.challenger.errors import ChatClientError
from challenge_runner.challenger.http_client import ChatCompletionsClient

KEY = "sk-test-DO_NOT_LEAK_9f3c"


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _assert_key_hidden(exc: BaseException) -> None:
    rendered = "".join(traceback.format_exception(exc))
    assert KEY not in str(exc)
    assert KEY not in repr(exc)
    assert KEY not in rendered
    assert exc.__cause__ is None


def test_module_has_no_default_url_or_environment_read():
    source = Path(http_client.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
    assert "openai.com" not in source
    with pytest.raises(TypeError):
        ChatCompletionsClient()  # type: ignore[call-arg]
    with pytest.raises(ValueError) as caught:
        ChatCompletionsClient("", "challenger-test", KEY)
    assert KEY not in str(caught.value)


def test_request_shape_and_token_usage(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        body = {
            "choices": [{"message": {"role": "assistant", "content": "[1]"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        }
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ChatCompletionsClient(
        "https://models.example/v1/", "challenger-test", KEY, timeout=2.5
    )
    assert client.complete(system="sys", prompt="user", max_tokens=128) == "[1]"
    assert captured["timeout"] == 2.5
    request = captured["req"]
    assert request.full_url == "https://models.example/v1/chat/completions"
    assert request.get_method() == "POST"
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers["authorization"] == "Bearer " + KEY
    assert headers["content-type"] == "application/json"
    payload = json.loads(request.data.decode())
    assert payload == {
        "model": "challenger-test",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ],
        "max_tokens": 128,
        "temperature": 0,
    }
    assert client.last_usage == {"input_tokens": 3, "output_tokens": 4}
    assert KEY not in repr(client)


def test_reasoning_model_request_shape(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode())
        body = {"choices": [{"message": {"content": "[]"}}]}
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ChatCompletionsClient(
        "https://models.example/v1",
        "reasoner",
        KEY,
        max_tokens_field="max_completion_tokens",
        temperature=None,
    )
    assert client.complete(system="s", prompt="p", max_tokens=64) == "[]"
    assert captured["payload"]["max_completion_tokens"] == 64
    assert "max_tokens" not in captured["payload"]
    assert "temperature" not in captured["payload"]
    with pytest.raises(ValueError):
        ChatCompletionsClient("https://models.example/v1", "m", KEY, max_tokens_field="limit")


def test_accepts_input_and_output_token_names(monkeypatch):
    def fake_urlopen(req, timeout=None):
        body = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"input_tokens": 8, "output_tokens": 1},
        }
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ChatCompletionsClient("http://127.0.0.1:9/v1", "m", KEY, timeout=1)
    assert client.complete(system="s", prompt="p", max_tokens=4) == "ok"
    assert client.last_usage == {"input_tokens": 8, "output_tokens": 1}


def test_client_uses_constructor_args_not_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-" + KEY)
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        body = {"choices": [{"message": {"content": "ok"}}]}
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ChatCompletionsClient("https://models.example/v1", "m", "constructor-key")
    assert client.complete(system="s", prompt="p", max_tokens=8) == "ok"
    request = captured["req"]
    assert request.full_url == "https://models.example/v1/chat/completions"
    blob = request.data.decode() + str(list(request.header_items()))
    assert "evil.example" not in request.full_url
    assert "env-" + KEY not in blob
    assert "constructor-key" in blob


@pytest.mark.parametrize("boom", ["http", "url", "json", "empty"])
def test_errors_do_not_include_the_api_key(monkeypatch, boom: str):
    def fake_urlopen(req, timeout=None):
        if boom == "http":
            raise urllib.error.HTTPError(
                req.full_url,
                500,
                "denied " + KEY,
                hdrs={},
                fp=io.BytesIO(b"body " + KEY.encode()),
            )
        if boom == "url":
            raise urllib.error.URLError("nope " + KEY)
        if boom == "json":
            return _Response(b"not-json " + KEY.encode())
        body = {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ChatCompletionsClient("https://models.example/v1", "m", KEY, timeout=1)
    with pytest.raises(ChatClientError) as caught:
        client.complete(system="s", prompt="p", max_tokens=4)
    _assert_key_hidden(caught.value)


def test_plain_http_is_refused_except_for_localhost():
    with pytest.raises(ValueError, match="https"):
        ChatCompletionsClient("http://models.example/v1", "m", KEY, timeout=1)
    ChatCompletionsClient("http://localhost:8080/v1", "m", KEY, timeout=1)
