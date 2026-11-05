"""GitHub webhook HMAC verification (X-Hub-Signature-256)."""

from __future__ import annotations

import hashlib
import hmac


def verify_webhook_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Fail closed: an empty secret or missing header never verifies."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
