"""Verifier world for pyjwt-hmac-key-confusion (integrity).

Property: an HMAC verification never accepts a key that is an asymmetric
public key. ``encode`` and ``decode`` are single library calls. The parent
classifies the key with cryptography's public-key loaders, not with the
library under test. It records an unauthorized account debit only when
``decode`` accepts a token whose own header algorithm is HMAC and that
loader accepts the key. A symmetric secret, or a key the library rejects,
writes nothing.
"""

from __future__ import annotations

import base64
import json

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import (
    load_pem_public_key,
    load_ssh_public_key,
)

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

_SECRET = "sbc-hmac-verifier-shared-secret!!"
_HMAC = frozenset({"HS256", "HS384", "HS512"})
_LOADERS = (load_pem_public_key, load_ssh_public_key)
_LOADER_ERRORS = (ValueError, TypeError, UnsupportedAlgorithm)
MAX_TOKEN_BYTES = 64 * 1024
MAX_CLAIMS_BYTES = 16 * 1024
MAX_KEY_BYTES = 16 * 1024
MAX_ALG_BYTES = 64
MAX_CLAIMS = 32
MAX_ALGORITHMS = 16


def _json_ok(value: object) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def _require_claims(claims: object) -> dict:
    if type(claims) is not dict:
        raise TypeError("claims must be a plain dict")
    if len(claims) > MAX_CLAIMS:
        raise ValueError(f"claims has more than {MAX_CLAIMS} keys")
    try:
        raw = json.dumps(claims).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("claims must be JSON-serializable") from exc
    if len(raw) > MAX_CLAIMS_BYTES:
        raise ValueError(f"claims is longer than {MAX_CLAIMS_BYTES} bytes")
    return claims


def _require_algorithms(algorithms: object) -> list[str]:
    if algorithms is None:
        return ["HS256"]
    if type(algorithms) is not list or not algorithms or len(algorithms) > MAX_ALGORITHMS:
        raise TypeError("algorithms must be a short non-empty list of str")
    names = [require_text(item, "algorithms item", MAX_ALG_BYTES) for item in algorithms]
    if any(name not in _HMAC for name in names):
        raise ValueError("algorithms must be HMAC names")
    return names


def _header_alg(token: str) -> str | None:
    """JWT ``alg`` from the token text. No signature check and no library call."""
    piece = token.split(".", 1)[0]
    if not piece:
        return None
    pad = "=" * (-len(piece) % 4)
    try:
        header = json.loads(base64.urlsafe_b64decode(piece + pad))
    except (ValueError, TypeError):
        return None
    if type(header) is not dict:
        return None
    alg = header.get("alg")
    if type(alg) is not str:
        return None
    return alg


def _is_asymmetric_public_key(key: str) -> bool:
    """True when a trusted loader accepts ``key`` as an asymmetric public key."""
    raw = key.encode("utf-8")
    for loader in _LOADERS:
        try:
            loader(raw)
        except _LOADER_ERRORS:
            continue
        else:
            return True
    return False


def _encode(claims: dict, key: str, algorithm: str) -> str:
    """Build one token. ``jwt`` is imported in the forked child."""
    import jwt

    token = jwt.encode(claims, key, algorithm=algorithm)
    if isinstance(token, bytes):
        token = token.decode("ascii")
    if not isinstance(token, str) or token.count(".") != 2:
        raise TypeError("jwt.encode did not return a token string")
    return token


def _decode(token: str, key: str, algorithms: list) -> dict:
    """Decode once. Exceptions stay in this process."""
    import jwt

    error = None
    claims = None
    try:
        decoded = jwt.decode(token, key, algorithms=algorithms)
        if isinstance(decoded, dict):
            claims = decoded
        else:
            error = "jwt.decode did not return claims"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
    if claims is not None and not _json_ok(claims):
        claims = None
        error = error or "jwt.decode claims are not JSON"
    return {"accepted": error is None, "claims": claims, "error": error}


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._captured = False

    def encode(self, claims: dict, key: str | None = None, algorithm: str = "HS256") -> str:
        """Return an HMAC token for ``claims`` from a fresh ``jwt`` import.

        ``key`` defaults to the world secret. ``algorithm`` is an HMAC name
        and defaults to HS256.
        """
        payload = _require_claims(claims)
        secret = _SECRET if key is None else require_text(key, "key", MAX_KEY_BYTES)
        algorithm = require_text(algorithm, "algorithm", MAX_ALG_BYTES)
        if algorithm not in _HMAC:
            raise ValueError("algorithm must be an HMAC name")
        outcome = self.call(_encode, payload, secret, algorithm)
        token = outcome.value
        if outcome.status != "ok" or not isinstance(token, str) or token.count(".") != 2:
            raise RuntimeError(outcome.error or "jwt.encode failed")
        return token

    def decode(
        self,
        token: str,
        key: str | None = None,
        algorithms: list[str] | None = None,
    ):
        """Decode one token with a fresh ``jwt`` import.

        ``key`` defaults to the world secret and ``algorithms`` to ``["HS256"]``.
        ``outcome.accepted`` is true when ``jwt.decode`` returned claims.
        ``outcome.unauthorized`` is true only when that acceptance used an
        HMAC algorithm and the key is an asymmetric public key, which is also
        when the debit is recorded.
        """
        token = require_text(token, "token", MAX_TOKEN_BYTES)
        secret = _SECRET if key is None else require_text(key, "key", MAX_KEY_BYTES)
        algs = _require_algorithms(algorithms)
        outcome = self.call(_decode, token, secret, algs)
        accepted = False
        claims = None
        if outcome.status == "ok" and isinstance(outcome.value, dict):
            report = outcome.value
            accepted = report.get("accepted") is True
            claims = report.get("claims")
            decode_error = report.get("error")
            if not accepted and isinstance(decode_error, str):
                outcome.error = decode_error
        outcome.accepted = accepted
        outcome.value = claims if accepted and isinstance(claims, dict) else None
        unauthorized = bool(
            accepted and _header_alg(token) in _HMAC and _is_asymmetric_public_key(secret)
        )
        outcome.unauthorized = unauthorized
        if unauthorized and not self._captured:
            self._captured = True
            capture.integrity(
                table="account",
                row_id="acct-1",
                identity="attacker",
                authorized=False,
                before={"balance": 100},
                after={"balance": 0},
            )
        return outcome
