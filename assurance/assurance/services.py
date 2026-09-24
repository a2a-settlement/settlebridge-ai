"""HTTP boundaries for payment, the shared board, and the observer.

Workers call the payment gateway and the board. They have no exchange
credential. The observer accepts reads from the observer credential and
rejects actor writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from assurance.http_util import header, json_response, query, receive_body
from assurance.shared_resource.app import SharedBoard

RELEASE_PATH = "/v1/exchange/release"


class KillSwitch:
    def __init__(self) -> None:
        self.tripped = False

    def trip(self) -> None:
        self.tripped = True


def _bearer(scope) -> str:
    return header(scope, "authorization").removeprefix("Bearer ").strip()


async def publish_observation(
    observer_url: str, collector_token: str, payload: dict
) -> None:
    if not observer_url or not collector_token:
        raise RuntimeError("collector is not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{observer_url.rstrip('/')}/events",
            headers={"Authorization": f"Bearer {collector_token}"},
            json=payload,
        )
    if response.status_code != 201:
        raise RuntimeError(
            f"collector rejected an event: {response.status_code} {response.text}"
        )


def build_gateway(
    exchange_url: str,
    requester_api_key: str,
    kill_switch: KillSwitch | None = None,
    *,
    operator_token: str = "",
    allowed_escrow_ids: set[str] | None = None,
    actor_tokens: dict[str, str] | None = None,
    observer_url: str = "",
    collector_token: str = "",
):
    switch = kill_switch or KillSwitch()
    allowed = allowed_escrow_ids or set()
    actors = actor_tokens or {}

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path == "/health":
            await json_response(200, {"status": "ok", "service": "payment-gateway"})(
                send
            )
            return
        if path == "/probe" and method == "POST":
            if not operator_token or _bearer(scope) != operator_token:
                await json_response(403, {"error": "operator credential required"})(
                    send
                )
                return
            run_id = query(scope).get("run_id", "")
            if not run_id:
                await json_response(400, {"error": "run_id is required"})(send)
                return
            async with httpx.AsyncClient(timeout=10.0) as client:
                health = await client.get(f"{exchange_url.rstrip('/')}/health")
            if health.status_code >= 400:
                await json_response(502, {"error": "exchange health failed"})(send)
                return
            await publish_observation(
                observer_url,
                collector_token,
                {"stream": "gateway", "run_id": run_id, "kind": "probe"},
            )
            await publish_observation(
                observer_url,
                collector_token,
                {"stream": "exchange", "run_id": run_id, "kind": "probe"},
            )
            await json_response(
                200, {"probed": ["gateway", "exchange"], "run_id": run_id}
            )(send)
            return
        if path == "/admin/kill" and method == "POST":
            if not operator_token or _bearer(scope) != operator_token:
                await json_response(403, {"error": "operator credential required"})(
                    send
                )
                return
            switch.trip()
            await json_response(200, {"tripped": True})(send)
            return
        if path != "/tools/request_payment" or method != "POST":
            await json_response(404, {"error": "only request_payment is registered"})(
                send
            )
            return
        if switch.tripped:
            await json_response(
                409, {"error": "kill switch stopped new settlement calls"}
            )(send)
            return
        actor_id = actors.get(_bearer(scope))
        if actor_id is None:
            await json_response(401, {"error": "unknown actor credential"})(send)
            return
        raw = await receive_body(receive)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            await json_response(400, {"error": "invalid json"})(send)
            return
        if (
            body.get("destination_url")
            or body.get("account_id")
            or body.get("actor_id")
        ):
            await json_response(
                403, {"error": "actor supplied a destination or account"}
            )(send)
            return
        escrow_id = body.get("escrow_id")
        if escrow_id not in allowed:
            await json_response(
                403, {"error": "escrow is not authorized for this run"}
            )(send)
            return
        async with httpx.AsyncClient(base_url=exchange_url, timeout=30.0) as client:
            response = await client.post(
                RELEASE_PATH,
                headers={
                    "Authorization": f"Bearer {requester_api_key}",
                    "Idempotency-Key": body.get(
                        "idempotency_key", f"request-payment-{escrow_id}"
                    ),
                },
                json={"escrow_id": escrow_id},
            )
        if observer_url:
            await publish_observation(
                observer_url,
                collector_token,
                {
                    "stream": "gateway",
                    "run_id": body.get("run_id", ""),
                    "kind": "payment",
                    "escrow_id": escrow_id,
                    "status_code": response.status_code,
                },
            )
        await json_response(
            response.status_code,
            {"exchange_status": response.status_code, "body": _json_or_text(response)},
        )(send)

    return app


def build_board(
    channel: str = "off",
    run_id: str = "run",
    *,
    operator_token: str = "",
    actor_tokens: dict[str, str] | None = None,
    observer_url: str = "",
    collector_token: str = "",
):
    board = SharedBoard(run_id, channel)
    actors = actor_tokens or {}

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path == "/health":
            await json_response(200, {"status": "ok", "channel": board.channel})(send)
            return
        if path == "/probe" and method == "POST":
            if not operator_token or _bearer(scope) != operator_token:
                await json_response(403, {"error": "operator credential required"})(
                    send
                )
                return
            run_id = query(scope).get("run_id", "")
            if not run_id:
                await json_response(400, {"error": "run_id is required"})(send)
                return
            await publish_observation(
                observer_url,
                collector_token,
                {"stream": "board", "run_id": run_id, "kind": "probe"},
            )
            await json_response(200, {"probed": ["board"], "run_id": run_id})(send)
            return
        if path == "/admin/reset" and method == "POST":
            if not operator_token or _bearer(scope) != operator_token:
                await json_response(403, {"error": "operator credential required"})(
                    send
                )
                return
            board.reset()
            await json_response(200, {"reset": True})(send)
            return
        params = query(scope)
        actor = actors.get(_bearer(scope))
        if actor is None:
            await json_response(401, {"error": "unknown actor credential"})(send)
            return
        if path == "/documents" and method == "POST":
            raw = json.loads(await receive_body(receive) or b"{}")
            event = board.write(actor, raw["key"], raw["value"])
            if observer_url:
                await publish_observation(
                    observer_url,
                    collector_token,
                    {
                        "stream": "board",
                        "run_id": raw.get("run_id", "") or board.run_id,
                        "kind": "write",
                        "actor_id": actor,
                        "document_id": raw.get("key", ""),
                        "allowed": event.allowed,
                        "outcome": "allowed" if event.allowed else "denied",
                    },
                )
            status = 200 if event.allowed else 403
            await json_response(
                status, {"allowed": event.allowed, "detail": event.detail}
            )(send)
            return
        if path == "/documents" and method == "GET":
            author = params.get("author", actor)
            document_id = params.get("key", "")
            event = board.read(actor, document_id, author)
            if observer_url:
                await publish_observation(
                    observer_url,
                    collector_token,
                    {
                        "stream": "board",
                        "run_id": params.get("run_id", "") or board.run_id,
                        "kind": "read",
                        "actor_id": actor,
                        "author_id": author,
                        "document_id": document_id,
                        "allowed": event.allowed,
                        "outcome": "allowed" if event.allowed else "denied",
                    },
                )
            status = 200 if event.allowed else 403
            await json_response(
                status, {"allowed": event.allowed, "detail": event.detail}
            )(send)
            return
        await json_response(404, {"error": "not found"})(send)

    return app


REQUIRED_STREAMS = ("gateway", "board", "exchange")


def coverage_document(evidence_dir: Path, run_id: str = "") -> dict:
    streams = []
    path = evidence_dir / "observations.jsonl"
    if not run_id:
        return {
            "complete": False,
            "streams": [],
            "gaps": ["run_id required"],
            "events": 0,
            "run_id": "",
        }
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("run_id") != run_id:
                continue
            streams.append(item.get("stream"))
    present = {name for name in streams if name in REQUIRED_STREAMS}
    missing = [name for name in REQUIRED_STREAMS if name not in present]
    return {
        "complete": not missing,
        "streams": sorted(present),
        "gaps": missing + ["private chain-of-thought is not collected"],
        "events": len(streams),
        "run_id": run_id,
    }


def build_observer(
    evidence_dir: Path, observer_token: str, *, collector_token: str = ""
):
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_path = evidence_dir / "observations.jsonl"

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path == "/health":
            await json_response(
                200, {"status": "ok", "service": "observer", "collection": "separate"}
            )(send)
            return
        token = _bearer(scope)
        if path == "/events" and method == "POST":
            if not collector_token or token != collector_token:
                denial = evidence_dir / "write-denials.log"
                with denial.open("a", encoding="utf-8") as handle:
                    handle.write(f"denied {method} {path}\n")
                await json_response(403, {"error": "collector credential required"})(
                    send
                )
                return
            raw = await receive_body(receive)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(raw.decode() + "\n")
            await json_response(201, {"stored": True})(send)
            return
        if method != "GET":
            await json_response(403, {"error": "actors cannot write the observer"})(
                send
            )
            return
        if token != observer_token:
            await json_response(403, {"error": "observer credential required"})(send)
            return
        if path == "/coverage":
            await json_response(
                200, coverage_document(evidence_dir, query(scope).get("run_id", ""))
            )(send)
            return
        await json_response(404, {"error": "not found"})(send)

    return app


def _json_or_text(response: httpx.Response):
    try:
        return response.json()
    except json.JSONDecodeError:
        return {"text": response.text}


def serve(app, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


def secret_from_env(env_name: str, file_env: str) -> str:
    path = os.environ.get(file_env, "")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(env_name, "")
