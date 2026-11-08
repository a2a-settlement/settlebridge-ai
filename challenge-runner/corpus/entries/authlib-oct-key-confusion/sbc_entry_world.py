"""Verifier world for authlib-oct-key-confusion (integrity).

Property: Authlib never accepts an asymmetric public key or certificate as the
secret for an HMAC-signed token.

``public_key`` only returns key material the world generated. ``encode`` and
``decode`` are single library calls, each in a forked child that imports
``authlib.jose`` fresh. The world reads the algorithm from the token text and
parses ``key`` with cryptography. It records an unauthorized ledger debit only
when a decode accepts a token whose algorithm is HMAC and whose key is a public
key or certificate. A symmetric secret, or a decode that rejects the token,
writes nothing.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    load_der_public_key,
    load_pem_public_key,
    load_ssh_public_key,
)
from cryptography.x509 import load_der_x509_certificate, load_pem_x509_certificate

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

_SECRET = "sbc-authlib-hmac-secret"
_HMAC_ALGS = frozenset({"HS256", "HS384", "HS512"})
_SSH_PREFIXES = (
    b"ssh-ed25519 ",
    b"ssh-rsa ",
    b"ssh-dss ",
    b"ecdsa-sha2-",
)
MAX_TOKEN_BYTES = 64 * 1024
MAX_CLAIMS_BYTES = 16 * 1024
MAX_KEY_BYTES = 64 * 1024
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
    return dict(claims)


def _require_algorithms(algorithms: object) -> list[str] | None:
    if algorithms is None:
        return None
    if type(algorithms) is not list or not algorithms or len(algorithms) > MAX_ALGORITHMS:
        raise TypeError("algorithms must be a short non-empty list of str")
    return [require_text(item, "algorithms item", MAX_ALG_BYTES) for item in algorithms]


def _header_alg(token: str) -> str | None:
    """Algorithm from the token header. No signature check and no library call."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[0]:
        return None
    pad = "=" * (-len(parts[0]) % 4)
    try:
        raw = base64.urlsafe_b64decode(parts[0] + pad)
        header = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if type(header) is not dict:
        return None
    alg = header.get("alg")
    if type(alg) is not str or alg not in _HMAC_ALGS:
        return None
    return alg


def _parses(loader, data: bytes) -> bool:
    try:
        loader(data)
    except Exception:
        return False
    return True


def _ssh_candidates(data: bytes) -> list[bytes]:
    found = [data]
    for marker in _SSH_PREFIXES:
        start = data.find(marker)
        if start < 0:
            continue
        line = data[start:].split(b"\n", 1)[0].strip()
        if line and line not in found:
            found.append(line)
    return found


def _is_asymmetric_public(key: str) -> bool:
    """True when ``key`` parses as a public key or an X.509 certificate.

    PEM loaders skip preamble before the BEGIN line. SSH text is parsed from
    the key line itself. A string that does not parse is a symmetric secret.
    """
    data = key.encode("utf-8")
    if any(_parses(loader, data) for loader in (load_pem_public_key, load_pem_x509_certificate)):
        return True
    if any(_parses(load_ssh_public_key, view) for view in _ssh_candidates(data)):
        return True
    return any(_parses(loader, data) for loader in (load_der_public_key, load_der_x509_certificate))


def _public_pem() -> str:
    private = ec.generate_private_key(ec.SECP256R1())
    encoded = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii")
    if not isinstance(encoded, str) or "-----BEGIN " not in encoded:
        raise RuntimeError("public key encoding failed")
    return encoded


def _encode(claims: dict, key: str, algorithm: str) -> str:
    """Sign one token. ``authlib.jose`` is imported in the forked child."""
    from authlib.jose import jwt

    token = jwt.encode({"alg": algorithm}, dict(claims), key)
    if isinstance(token, bytes):
        token = token.decode("ascii")
    if not isinstance(token, str) or token.count(".") != 2:
        raise TypeError("jwt.encode did not return a compact token")
    if any(not part for part in token.split(".")):
        raise TypeError("jwt.encode did not return a compact token")
    return token


def _decode(token: str, key: str, algorithms: list | None) -> dict:
    """Decode once. Library errors stay in this process and are reported."""
    from authlib.jose import JsonWebToken, jwt

    error = None
    claims = None
    try:
        checker = jwt if algorithms is None else JsonWebToken(algorithms)
        decoded = checker.decode(token, key)
        if isinstance(decoded, dict):
            claims = dict(decoded)
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
        self._public_pem: str | None = None
        self._captured = False

    def public_key(self) -> str:
        """Return this world's PEM public key.

        The key is generated in the parent. It is not an HMAC secret.
        """
        if self._public_pem is None:
            self._public_pem = _public_pem()
        return self._public_pem

    def encode(self, claims: dict, key: str | None = None, algorithm: str = "HS256") -> str:
        """Return a compact token for ``claims`` from a fresh ``authlib.jose`` import.

        ``key`` defaults to the world secret. ``algorithm`` defaults to HS256.
        """
        payload = _require_claims(claims)
        secret = _SECRET if key is None else require_text(key, "key", MAX_KEY_BYTES)
        algorithm = require_text(algorithm, "algorithm", MAX_ALG_BYTES)
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
        """Decode one token with a fresh ``authlib.jose`` import.

        ``key`` defaults to the world secret. ``algorithms`` defaults to the
        algorithms registered on ``authlib.jose.jwt``. ``outcome.accepted`` is
        true when decode returned claims. ``outcome.unauthorized`` is true only
        when that acceptance is an HMAC token whose key parses as an asymmetric
        public key or certificate, which is also when the debit is recorded.
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
        unauthorized = bool(accepted and _header_alg(token) and _is_asymmetric_public(secret))
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
