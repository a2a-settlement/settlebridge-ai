"""Small ASGI helpers. The assurance package does not depend on FastAPI."""

from __future__ import annotations

import json
from urllib.parse import parse_qs


def read_body(receive) -> bytes:
    raise RuntimeError("use the async reader")


async def receive_body(receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


def json_response(
    status: int, payload: dict, *, headers: list[tuple[bytes, bytes]] | None = None
):
    body = json.dumps(payload).encode()
    extra = headers or []

    async def send(send_fn):
        await send_fn(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), *extra],
            }
        )
        await send_fn({"type": "http.response.body", "body": body})

    return send


def query(scope) -> dict[str, str]:
    raw = scope.get("query_string", b"").decode()
    parsed = parse_qs(raw)
    return {key: values[-1] for key, values in parsed.items()}


def header(scope, name: str) -> str:
    want = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key.lower() == want:
            return value.decode()
    return ""
