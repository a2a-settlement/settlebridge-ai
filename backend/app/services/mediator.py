"""HTTP client for the A2A Settlement Mediator service.

Calls to the mediator's operator routes (``/mediate``, ``/audits``) carry a
``v2`` signature that covers the method and path as well as the body.
``/api/v1/verify-patch`` keeps its own ``{timestamp}.{body}`` signature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


def _nonce() -> str:
    """A fresh value per request, so two identical calls never share a signature."""
    return secrets.token_urlsafe(24)


def _operator_headers(method: str, target: str, body: bytes = b"") -> dict[str, str]:
    """Signature headers for one call to a mediator operator route.

    The HMAC is SHA-256, keyed with the webhook secret, over ``v2``, the
    timestamp, the nonce, the upper-case method and ``target``, each followed
    by a newline, then the exact body bytes. ``target`` is the path the
    mediator receives, percent-encoded as sent, with ``?query`` when present.

    Returns no headers when the secret is not configured, so a local mediator
    running without authentication still works. A mediator that requires
    signatures rejects such a call.
    """
    secret = settings.MEDIATOR_WEBHOOK_SECRET
    if not secret:
        return {}
    timestamp = str(_timestamp())
    nonce = _nonce()
    message = f"v2\n{timestamp}\n{nonce}\n{method.upper()}\n{target}\n".encode() + body
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {
        "X-A2ASE-Timestamp": timestamp,
        "X-A2ASE-Nonce": nonce,
        "X-A2ASE-Signature": f"v2={signature}",
    }


async def _operator_request(
    method: str, target: str, payload: dict[str, Any] | None = None
) -> httpx.Response:
    """Send one signed request to ``target`` under ``MEDIATOR_URL``.

    The body is serialized once and those bytes are both signed and sent.
    ``target`` is signed as given, without any path prefix in ``MEDIATOR_URL``:
    it must be the path the mediator itself sees.
    """
    body = b""
    headers: dict[str, str] = {}
    if payload:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    headers.update(_operator_headers(method, target, body))
    url = f"{settings.MEDIATOR_URL.rstrip('/')}{target}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await client.request(method, url, content=body or None, headers=headers)


def _escrow_target(prefix: str, escrow_id: str) -> str:
    return f"{prefix}/{quote(escrow_id, safe='')}"


async def trigger_mediation(
    escrow_id: str,
    mode: str | None = None,
    task_type: str | None = None,
) -> dict[str, Any]:
    """Trigger mediation for an escrow.

    Args:
        escrow_id: The escrow to mediate.
        mode: Optional scoring mode — ``"training"`` or ``"production"``.
            When ``"training"``, the mediator produces a ``structured_diagnostic``
            in its response with actionable gaps for self-improvement.
        task_type: Optional task type string forwarded to the mediator to
            enable task-specific diagnostic output.
    """
    body: dict[str, Any] = {}
    if mode is not None:
        body["mode"] = mode
    if task_type is not None:
        body["task_type"] = task_type

    resp = await _operator_request("POST", _escrow_target("/mediate", escrow_id), body)
    resp.raise_for_status()
    return resp.json()


def _normalise_mediation_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten the mediator's response into a stable shape.

    The real a2a-settlement-mediator nests the scored fields inside a ``verdict``
    sub-object:
        { "verdict": { "confidence": 0.75, "reasoning": "...", ... }, ... }

    The mock mediator (used for local testing) emits them at the top level:
        { "confidence": 0.75, "reasoning": "...", "structured_diagnostic": {...} }

    This function produces a consistent flat dict regardless of which mediator
    is answering, so callers can always use ``result["confidence"]`` etc.
    """
    verdict: dict[str, Any] = raw.get("verdict") or {}

    confidence = (
        raw.get("confidence")
        if raw.get("confidence") is not None
        else float(verdict.get("confidence", 0.0))
    )

    reasoning = raw.get("reasoning") or verdict.get("reasoning")

    # Real mediator may return structured_diagnostic inside verdict or not at all;
    # build one from available fields so record_score always gets a consistent dict.
    structured = raw.get("structured_diagnostic") or verdict.get("structured_diagnostic") or {}
    if not structured:
        factors = verdict.get("factors", [])
        if factors:
            structured = {"actionable_gaps": factors, "details": {"factors": factors}}

    return {
        "confidence": float(confidence),
        "reasoning": reasoning,
        "structured_diagnostic": structured,
        "verdict": verdict,
        "_raw": raw,
    }


async def trigger_training_mediation(escrow_id: str, task_type: str) -> dict[str, Any]:
    """Trigger mediation for a training iteration.

    Returns a normalised dict with top-level ``confidence``, ``reasoning``,
    and ``structured_diagnostic`` keys regardless of which mediator is live.
    """
    resp = await _operator_request(
        "POST",
        _escrow_target("/mediate", escrow_id),
        {"mode": "training", "task_type": task_type},
    )
    resp.raise_for_status()
    return _normalise_mediation_result(resp.json())


async def get_audit(escrow_id: str) -> dict[str, Any] | None:
    """Fetch the mediation audit record for an escrow."""
    resp = await _operator_request("GET", _escrow_target("/audits", escrow_id))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _timestamp() -> int:
    """Unix seconds for one signed call. A retry is a new call, so it signs again."""
    return int(time.time())


async def verify_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """POST a canonical JSON body to the mediator verify-patch endpoint.

    The body is serialized once. The HMAC is SHA-256 over ``f"{timestamp}."``
    plus those exact bytes, keyed with the webhook secret.
    """
    secret = settings.MEDIATOR_WEBHOOK_SECRET
    if not secret:
        raise ValueError("MEDIATOR_WEBHOOK_SECRET not configured")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    timestamp = _timestamp()
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("ascii") + body,
        hashlib.sha256,
    ).hexdigest()
    url = f"{settings.MEDIATOR_URL.rstrip('/')}/api/v1/verify-patch"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-A2ASE-Timestamp": str(timestamp),
                "X-A2ASE-Signature": f"sha256={signature}",
            },
        )
        resp.raise_for_status()
        return resp.json()
