"""OpenAI-compatible chat completions client. Stdlib urllib only.

Configured only by constructor arguments. This module does not read the
environment and has no default service URL.
"""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit

from challenge_runner.challenger.errors import ChatClientError
from challenge_runner.challenger.limits import MAX_RESPONSE_BYTES


class ChatCompletionsClient:
    """ModelClient that POSTs to ``{base_url}/chat/completions``."""

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60.0):
        self._base_url = _require_base_url(base_url)
        self._model = _require_text(model, "model")
        self._api_key = _require_text(api_key, "api_key")
        self._timeout = _require_timeout(timeout)
        self.last_usage: dict[str, int] | None = None

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        if not isinstance(system, str) or not isinstance(prompt, str):
            raise ValueError("system and prompt must be strings")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        self.last_usage = None
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        request = urllib.request.Request(
            self._base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header("Authorization", "Bearer " + self._api_key)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = _read_limited(response, MAX_RESPONSE_BYTES)
        except ChatClientError:
            raise
        except Exception:
            raise ChatClientError("model request failed") from None
        return self._parse(raw)

    def __repr__(self) -> str:
        return f"ChatCompletionsClient(base_url={self._base_url!r}, model={self._model!r})"

    def _parse(self, raw: bytes) -> str:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ChatClientError("model response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise ChatClientError("model response was not valid JSON")
        self.last_usage = _usage(payload.get("usage"))
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ChatClientError("model response did not include a message")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ChatClientError("model response did not include a message")
        return content


def _require_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url or base_url.strip() != base_url:
        raise ValueError("base_url is required")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("base_url must be an absolute http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("base_url must not include credentials, a query, or a fragment")
    # The API key travels in a header, so plain http is only for a local proxy.
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("base_url must use https unless it is localhost")
    return base_url.rstrip("/")


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is required")
    return value


def _require_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a positive number")
    if timeout <= 0:
        raise ValueError("timeout must be a positive number")
    return float(timeout)


def _read_limited(response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        block = response.read(min(65536, limit - total + 1))
        if not block:
            break
        total += len(block)
        if total > limit:
            raise ChatClientError("model response was too large")
        chunks.append(block)
    return b"".join(chunks)


def _usage(raw: object) -> dict[str, int]:
    usage = raw if isinstance(raw, dict) else {}
    return {
        "input_tokens": _usage_count(usage, "prompt_tokens", "input_tokens"),
        "output_tokens": _usage_count(usage, "completion_tokens", "output_tokens"),
    }


def _usage_count(usage: dict, primary: str, alternate: str) -> int:
    for key in (primary, alternate):
        count = _token(usage.get(key))
        if count is not None:
            return count
    return 0


def _token(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value
