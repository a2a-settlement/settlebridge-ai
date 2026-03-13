"""Wraps Anthropic SDK for streaming conversation with structured draft extraction.

Supports two modes:
- marketplace: extracts <bounty_draft> (original bounty assist flow)
- gateway: extracts <policy_draft> and <alert_rule> (gateway ops flow)
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Literal

import anthropic

from app.config import settings
from app.services.prompts.bounty_assist import SYSTEM_PROMPT as BOUNTY_SYSTEM_PROMPT
from app.services.prompts.gateway_assist import SYSTEM_PROMPT as GATEWAY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

AssistMode = Literal["marketplace", "gateway"]

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def _system_prompt_for_mode(mode: AssistMode) -> str:
    if mode == "gateway":
        return GATEWAY_SYSTEM_PROMPT
    return BOUNTY_SYSTEM_PROMPT


@dataclass
class EngineResponse:
    """Holds the parsed result of a single Claude turn."""
    conversation_text: str
    bounty_draft: dict | None = None
    policy_draft: dict | None = None
    alert_rule: dict | None = None
    raw_text: str = ""


_BOUNTY_DRAFT_PATTERN = re.compile(
    r"<bounty_draft>\s*(.*?)\s*</bounty_draft>",
    re.DOTALL,
)
_POLICY_DRAFT_PATTERN = re.compile(
    r"<policy_draft>\s*(.*?)\s*</policy_draft>",
    re.DOTALL,
)
_ALERT_RULE_PATTERN = re.compile(
    r"<alert_rule>\s*(.*?)\s*</alert_rule>",
    re.DOTALL,
)


def parse_response(raw: str, mode: AssistMode = "marketplace") -> EngineResponse:
    """Split the raw model output into conversational text and structured drafts."""
    conversation = raw
    bounty_draft: dict | None = None
    policy_draft: dict | None = None
    alert_rule: dict | None = None

    if mode == "marketplace":
        match = _BOUNTY_DRAFT_PATTERN.search(raw)
        if match:
            try:
                bounty_draft = json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.warning("Failed to parse bounty_draft JSON from model output")
            conversation = raw[: match.start()].rstrip()
    else:
        # Gateway mode: extract policy_draft and/or alert_rule
        earliest_start = len(raw)

        pm = _POLICY_DRAFT_PATTERN.search(raw)
        if pm:
            try:
                policy_draft = json.loads(pm.group(1))
            except json.JSONDecodeError:
                logger.warning("Failed to parse policy_draft JSON from model output")
            earliest_start = min(earliest_start, pm.start())

        am = _ALERT_RULE_PATTERN.search(raw)
        if am:
            try:
                alert_rule = json.loads(am.group(1))
            except json.JSONDecodeError:
                logger.warning("Failed to parse alert_rule JSON from model output")
            earliest_start = min(earliest_start, am.start())

        if earliest_start < len(raw):
            conversation = raw[:earliest_start].rstrip()

    return EngineResponse(
        conversation_text=conversation,
        bounty_draft=bounty_draft,
        policy_draft=policy_draft,
        alert_rule=alert_rule,
        raw_text=raw,
    )


def _build_messages(history: list[dict], new_user_message: str) -> list[dict]:
    """Build the Anthropic messages list from session history + new message."""
    messages: list[dict] = []
    for msg in history:
        role = msg["role"]
        content = msg["content"]
        if role == "assistant":
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": content})
    messages.append({"role": "user", "content": new_user_message})
    return messages


async def stream_response(
    history: list[dict],
    user_message: str,
    mode: AssistMode = "marketplace",
) -> AsyncGenerator[str, None]:
    """Stream Claude's response token-by-token, yielding text chunks.

    The caller is responsible for accumulating the full text and calling
    parse_response() on the complete output.
    """
    client = _get_client()
    messages = _build_messages(history, user_message)

    async with client.messages.stream(
        model=settings.ASSIST_MODEL,
        max_tokens=settings.ASSIST_MAX_TOKENS,
        system=_system_prompt_for_mode(mode),
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def get_response(
    history: list[dict],
    user_message: str,
    mode: AssistMode = "marketplace",
) -> EngineResponse:
    """Non-streaming variant: get the full response at once."""
    client = _get_client()
    messages = _build_messages(history, user_message)

    response = await client.messages.create(
        model=settings.ASSIST_MODEL,
        max_tokens=settings.ASSIST_MAX_TOKENS,
        system=_system_prompt_for_mode(mode),
        messages=messages,
    )

    raw = response.content[0].text
    return parse_response(raw, mode=mode)
