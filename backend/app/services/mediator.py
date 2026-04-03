"""HTTP client for the A2A Settlement Mediator service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


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

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        url = f"{settings.MEDIATOR_URL}/mediate/{escrow_id}"
        resp = await client.post(url, json=body if body else None)
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
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        url = f"{settings.MEDIATOR_URL}/mediate/{escrow_id}"
        resp = await client.post(url, json={"mode": "training", "task_type": task_type})
        resp.raise_for_status()
        return _normalise_mediation_result(resp.json())


async def get_audit(escrow_id: str) -> dict[str, Any] | None:
    """Fetch the mediation audit record for an escrow."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        url = f"{settings.MEDIATOR_URL}/audits/{escrow_id}"
        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
