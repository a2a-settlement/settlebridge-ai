"""Thin provider adapters. Results name an endpoint, not a brand.

Confirmatory runs refuse moving aliases. Token and reasoning budgets are
recorded and are not claimed to be matched across providers.
"""

from __future__ import annotations

from dataclasses import dataclass

MOVING_SUFFIXES = ("-latest", "-preview-latest")

CANDIDATES = {
    "openai": "gpt-5-2025-08-07",
    "gemini": "gemini-2.5-pro",
    "anthropic": "claude-sonnet-4-6",
    "xai": "grok-4.6",
}


@dataclass
class ResolvedModel:
    provider: str
    requested_id: str
    resolved_id: str
    endpoint: str
    fingerprint: str | None
    moving_alias: bool


def is_moving_alias(model_id: str) -> bool:
    return model_id.endswith("-latest") or model_id in {"grok-latest", "latest"}


def resolve_model(
    provider: str, model_id: str, *, fingerprint: str | None = None
) -> ResolvedModel:
    if is_moving_alias(model_id) or any(
        model_id.endswith(suffix) for suffix in MOVING_SUFFIXES
    ):
        raise ValueError(
            f"{model_id} is a moving alias and cannot start a confirmatory run"
        )
    return ResolvedModel(
        provider=provider,
        requested_id=model_id,
        resolved_id=model_id,
        endpoint=f"https://example.invalid/{provider}",
        fingerprint=fingerprint,
        moving_alias=False,
    )


MATCHED_LIMITS = {
    "tool_calls": 20,
    "turns": 12,
    "wall_clock_seconds": 600,
    "team_size": 3,
}
