"""Wraps Anthropic SDK for streaming conversation with bounty_draft extraction."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import anthropic

from app.config import settings
from app.services.prompts.bounty_assist import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


@dataclass
class EngineResponse:
    """Holds the parsed result of a single Claude turn."""
    conversation_text: str
    bounty_draft: dict | None
    raw_text: str


_DRAFT_PATTERN = re.compile(
    r"<bounty_draft>\s*(.*?)\s*</bounty_draft>",
    re.DOTALL,
)


def parse_response(raw: str) -> EngineResponse:
    """Split the raw model output into conversational text and structured draft."""
    match = _DRAFT_PATTERN.search(raw)
    draft: dict | None = None
    conversation = raw

    if match:
        try:
            draft = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning("Failed to parse bounty_draft JSON from model output")
        conversation = raw[: match.start()].rstrip()

    return EngineResponse(
        conversation_text=conversation,
        bounty_draft=draft,
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
        system=SYSTEM_PROMPT,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def get_response(
    history: list[dict],
    user_message: str,
) -> EngineResponse:
    """Non-streaming variant: get the full response at once."""
    client = _get_client()
    messages = _build_messages(history, user_message)

    response = await client.messages.create(
        model=settings.ASSIST_MODEL,
        max_tokens=settings.ASSIST_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    raw = response.content[0].text
    return parse_response(raw)
