"""Verifier world for python-jose-hmac-key-confusion (integrity).

Property: ``jose.jwt`` never accepts an asymmetric public key as an HMAC
secret, so an HS256 token signed with that key does not verify.

``public_key`` returns one fresh public key. ``encode`` and ``decode`` are
single library calls. The world reads the token header itself and parses the
key with the cryptography library, not with python-jose. A shared-secret
encode/decode writes nothing. The debit is recorded only when decode accepts
an HMAC token and the key bytes are an asymmetric public key or certificate.
"""

from __future__ import annotations

import base64
import json

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_bytes, require_text

_SECRET = "sbc-fixed-secret"
_HMAC_ALGS = {"HS256", "HS384", "HS512"}
_ENCODINGS = {"DER", "PEM"}
MAX_TOKEN_BYTES = 64 * 1024
MAX_CLAIMS_BYTES = 16 * 1024
MAX_KEY_BYTES = 8 * 1024
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


def _require_key(key: object) -> str | bytes:
    if key is None:
        return _SECRET
    if type(key) is bytes:
        return require_bytes(key, "key", MAX_KEY_BYTES)
    if type(key) is str:
        return require_text(key, "key", MAX_KEY_BYTES)
    raise TypeError("key must be a plain str or bytes")


def _require_algorithms(algorithms: object) -> list[str]:
    if algorithms is None:
        return ["HS256"]
    if type(algorithms) is not list or not algorithms or len(algorithms) > MAX_ALGORITHMS:
        raise TypeError("algorithms must be a short non-empty list of str")
    return [require_text(item, "algorithms item", MAX_ALG_BYTES) for item in algorithms]


def _key_bytes(key: str | bytes) -> bytes:
    if type(key) is bytes:
        return key
    return key.encode("utf-8")


def _is_asymmetric_public_key(key: str | bytes) -> bool:
    """True when ``key`` is a public key or certificate.

    Uses the cryptography parsers, which are not the library under test.
    The bytes are the ones HMAC will see: a str key is UTF-8, matching
    python-jose.
    """
    raw = _key_bytes(key)
    if not raw:
        return False
    from cryptography.hazmat.primitives.serialization import (
        load_der_public_key,
        load_pem_public_key,
        load_ssh_public_key,
    )
    from cryptography.x509 import load_der_x509_certificate, load_pem_x509_certificate

    for load in (
        load_der_public_key,
        load_pem_public_key,
        load_ssh_public_key,
        load_der_x509_certificate,
        load_pem_x509_certificate,
    ):
        try:
            load(raw)
        except Exception:
            continue
        return True
    return False


def _generate_public_key(encoding: str) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encoded = serialization.Encoding.PEM if encoding == "PEM" else serialization.Encoding.DER
    return private_key.public_key().public_bytes(
        encoded,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _hmac_public_key_accepted(
    accepted: bool, alg: str | None, key: str | bytes, algorithms: list[str]
) -> bool:
    """True only for an accepted HMAC verification whose key is asymmetric.

    An octet secret never parses as a public key, so a normal encode/decode
    does not qualify. The caller must also have allowed that HMAC algorithm.
    """
    if not accepted or alg not in _HMAC_ALGS or alg not in algorithms:
        return False
    if key == _SECRET:
        return False
    return _is_asymmetric_public_key(key)


def _header_alg(token: str) -> str | None:
    """JWT ``alg`` from the token text. No signature check and no library call."""
    part = token.split(".", 1)[0]
    if not part or token.count(".") != 2:
        return None
    pad = "=" * (-len(part) % 4)
    try:
        raw = base64.urlsafe_b64decode(part + pad)
        header = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if type(header) is not dict:
        return None
    alg = header.get("alg")
    if type(alg) is not str:
        return None
    return alg


def _encode(claims: dict, key: str | bytes, algorithm: str) -> str:
    """Build one token. ``jose`` is imported in the forked child."""
    from jose import jwt

    token = jwt.encode(claims, key, algorithm=algorithm)
    if isinstance(token, bytes):
        token = token.decode("ascii")
    if not isinstance(token, str) or token.count(".") != 2:
        raise TypeError("jwt.encode did not return a token string")
    return token


def _decode(token: str, key: str | bytes, algorithms: list) -> dict:
    """Decode once. Exceptions stay in this process."""
    from jose import jwt

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
        error = error or "claims were not JSON"
    return {
        "accepted": error is None and isinstance(claims, dict),
        "claims": claims,
        "error": error,
    }


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._captured = False

    def public_key(self, encoding: str = "DER") -> bytes:
        """Return one fresh RSA public key in ``DER`` or ``PEM`` form."""
        encoding = require_text(encoding, "encoding", MAX_ALG_BYTES).upper()
        if encoding not in _ENCODINGS:
            raise ValueError("encoding must be DER or PEM")
        key = _generate_public_key(encoding)
        if len(key) > MAX_KEY_BYTES or not _is_asymmetric_public_key(key):
            raise RuntimeError("generated public key did not parse")
        return key

    def encode(
        self,
        claims: dict,
        key: str | bytes | None = None,
        algorithm: str = "HS256",
    ) -> str:
        """Return a token for ``claims`` from a fresh ``jose`` import.

        ``key`` defaults to the world secret. ``algorithm`` defaults to HS256.
        """
        payload = _require_claims(claims)
        secret = _require_key(key)
        algorithm = require_text(algorithm, "algorithm", MAX_ALG_BYTES)
        outcome = self.call(_encode, payload, secret, algorithm)
        token = outcome.value
        if outcome.status != "ok" or not isinstance(token, str) or token.count(".") != 2:
            raise RuntimeError(outcome.error or "jwt.encode failed")
        return token

    def decode(
        self,
        token: str,
        key: str | bytes | None = None,
        algorithms: list[str] | None = None,
    ):
        """Decode one token with a fresh ``jose`` import.

        ``key`` defaults to the world secret and ``algorithms`` to ``["HS256"]``.
        ``outcome.accepted`` is true when ``jwt.decode`` returned claims.
        ``outcome.unauthorized`` is true only when that acceptance is an HMAC
        algorithm the caller allowed and the key is an asymmetric public key
        or certificate, which is also when the debit is recorded. A shared
        secret does not qualify.
        """
        token = require_text(token, "token", MAX_TOKEN_BYTES)
        secret = _require_key(key)
        algs = _require_algorithms(algorithms)
        alg = _header_alg(token)
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
        unauthorized = _hmac_public_key_accepted(accepted, alg, secret, algs)
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
