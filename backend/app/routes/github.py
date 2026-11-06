"""GitHub App webhook ingestion.

The delivery row and any job are committed in one transaction. Handling runs
in the job worker, never in this request.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.github import GitHubJob, GitHubWebhookDelivery, JobKind, JobStatus
from app.services.github_signature import verify_webhook_signature

logger = logging.getLogger(__name__)

router = APIRouter()

_ISSUE_CHANGED_ACTIONS = frozenset({"unlabeled", "edited", "reopened"})
_PR_UPDATED_ACTIONS = frozenset({"opened", "synchronize", "reopened", "edited"})
_CLEAR_PREFIX = "/settlebridge clear"


def _action_of(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if isinstance(action, str):
        return action
    return None


def _is_clear_comment(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    comment = data.get("comment")
    if not isinstance(comment, dict):
        return False
    body = comment.get("body")
    if not isinstance(body, str):
        return False
    return body.strip().startswith(_CLEAR_PREFIX)


def _declared_content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return None


async def _read_limited_body(request: Request) -> bytes:
    """Read the webhook body, rejecting anything over the configured cap before HMAC."""
    limit = int(settings.GITHUB_WEBHOOK_MAX_BODY_BYTES)
    declared = _declared_content_length(request)
    if declared is not None and declared > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Webhook body too large",
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Webhook body too large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def job_kind_for(event: str, action: str | None, data: object) -> JobKind | None:
    """Map a verified webhook to a job kind. None means record-only."""
    if event in ("installation", "installation_repositories"):
        return JobKind.INSTALLATION
    if event == "issues":
        if action == "labeled":
            return JobKind.ISSUE_LABELED
        if action in _ISSUE_CHANGED_ACTIONS:
            return JobKind.ISSUE_CHANGED
        if action == "closed":
            return JobKind.ISSUE_CLOSED
        return None
    if event == "pull_request":
        if action in _PR_UPDATED_ACTIONS:
            return JobKind.PR_UPDATED
        if action == "closed":
            return JobKind.PR_CLOSED
        return None
    if event == "check_suite" and action == "completed":
        return JobKind.CHECKS_COMPLETED
    if event == "issue_comment" and action == "created" and _is_clear_comment(data):
        return JobKind.REVIEW_CLEAR_COMMENT
    if event in ("dependabot_alert", "code_scanning_alert") and action in ("created", "fixed"):
        return JobKind.SECURITY_ALERT
    return None


@router.post("/webhook")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    body = await _read_limited_body(request)
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_webhook_signature(settings.GITHUB_WEBHOOK_SECRET, body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    event = (request.headers.get("X-GitHub-Event") or "").strip()
    delivery_id = (request.headers.get("X-GitHub-Delivery") or "").strip()
    if not event or not delivery_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event or X-GitHub-Delivery",
        )

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON",
        ) from None

    if event == "ping":
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "pong"})

    action = _action_of(data)
    kind = job_kind_for(event, action, data)
    try:
        db.add(GitHubWebhookDelivery(delivery_id=delivery_id, event=event, action=action))
        # The job's foreign key needs the delivery row first, and no ORM
        # relationship tells the session to order the two inserts.
        await db.flush()
        if kind is not None:
            db.add(
                GitHubJob(
                    delivery_id=delivery_id,
                    kind=kind.value,
                    payload={"event": event, "action": action, "data": data},
                    status=JobStatus.PENDING.value,
                )
            )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if await db.get(GitHubWebhookDelivery, delivery_id) is None:
            raise
        logger.info("Duplicate GitHub delivery %s", delivery_id)
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "duplicate"})

    outcome = "queued" if kind is not None else "ignored"
    logger.info("GitHub delivery %s %s -> %s", delivery_id, event, outcome)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": outcome, "delivery_id": delivery_id},
    )
