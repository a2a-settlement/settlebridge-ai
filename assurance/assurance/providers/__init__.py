"""Provider adapters record the pinned endpoint. They do not call a network in tests."""

from assurance.providers.base import (
    CANDIDATES,
    MATCHED_LIMITS,
    ResolvedModel,
    is_moving_alias,
    resolve_model,
)

__all__ = [
    "CANDIDATES",
    "MATCHED_LIMITS",
    "ResolvedModel",
    "is_moving_alias",
    "resolve_model",
    "prepare_call",
]


def prepare_call(
    provider: str,
    model_id: str,
    *,
    credential_present: bool,
    fingerprint: str | None = None,
) -> dict:
    if is_moving_alias(model_id):
        raise ValueError(
            f"{model_id} is a moving alias and cannot start a confirmatory run"
        )
    if not credential_present:
        return {
            "provider": provider,
            "requested_id": model_id,
            "status": "not_evaluated",
            "ranking": None,
        }
    resolved = resolve_model(provider, model_id, fingerprint=fingerprint)
    return {
        "provider": resolved.provider,
        "requested_id": resolved.requested_id,
        "resolved_id": resolved.resolved_id,
        "endpoint": resolved.endpoint,
        "fingerprint": resolved.fingerprint,
        "temperature": 0,
        "tool_format": "function_calling",
        "truncation": None,
        "unsupported_settings": [],
        "limits": dict(MATCHED_LIMITS),
        "status": "ready",
        "ranking": None,
    }
