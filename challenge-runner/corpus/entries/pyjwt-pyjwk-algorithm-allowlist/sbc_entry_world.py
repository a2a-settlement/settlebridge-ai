"""Verifier world for pyjwt-pyjwk-algorithm-allowlist (integrity).

Property: ``jwt.decode`` with a PyJWK key verifies only with an algorithm in
the caller's algorithms allow-list.

``encode`` builds one token. ``header_algorithm`` is written into the header
and defaults to ``algorithm``, which is the algorithm that signs the bytes.
``jwk`` returns an oct JWK for the same key. ``decode`` checks one token with
a fresh ``jwt`` import, using either a raw key or that JWK.

The world reads the algorithm the library bound on the PyJWK. It records an
unauthorized account debit only when decode returns claims, the caller passed
an algorithms list, and that PyJWK algorithm is not in the list. A raw key, a
rejected token, or a PyJWK algorithm that is in the list writes nothing.
"""

from __future__ import annotations

import json

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

_SECRET = "sbc-pyjwk-secret"
_HMAC_ALGS = frozenset({"HS256", "HS384", "HS512"})
MAX_TOKEN_BYTES = 64 * 1024
MAX_CLAIMS_BYTES = 16 * 1024
MAX_KEY_BYTES = 8 * 1024
MAX_ALG_BYTES = 64
MAX_ALGORITHMS = 16
MAX_CLAIMS = 32
MAX_JWK_FIELDS = 24
MAX_JWK_VALUE = 8 * 1024


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


def _require_alg(value: object, name: str) -> str:
    text = require_text(value, name, MAX_ALG_BYTES)
    if not text or any(char.isspace() for char in text):
        raise ValueError(f"{name} must be an algorithm name")
    return text


def _require_algorithms(algorithms: object) -> list[str] | None:
    if algorithms is None:
        return None
    if type(algorithms) is not list or not algorithms or len(algorithms) > MAX_ALGORITHMS:
        raise TypeError("algorithms must be a short non-empty list of str")
    return [_require_alg(item, "algorithms item") for item in algorithms]


def _require_jwk(jwk: object) -> dict | None:
    if jwk is None:
        return None
    if type(jwk) is not dict:
        raise TypeError("jwk must be a plain dict")
    if not jwk or len(jwk) > MAX_JWK_FIELDS:
        raise ValueError(f"jwk must have 1 to {MAX_JWK_FIELDS} fields")
    checked: dict[str, str] = {}
    for index, (key, value) in enumerate(jwk.items()):
        name = _require_alg(key, f"jwk key {index}")
        checked[name] = require_text(value, name, MAX_JWK_VALUE)
    return checked


def _encode(claims: dict, key: str, algorithm: str, header_algorithm: str | None) -> str:
    """Build one token. ``jwt`` is imported in the forked child."""
    header_alg = header_algorithm or algorithm
    if header_alg == algorithm:
        import jwt

        token = jwt.encode(claims, key, algorithm=algorithm)
        if isinstance(token, bytes):
            token = token.decode("ascii")
        if not isinstance(token, str) or token.count(".") != 2:
            raise TypeError("jwt.encode did not return a token string")
        return token

    # Sign with ``algorithm`` and write ``header_alg`` into the header.
    # ``jwt.encode`` would switch the signature to whatever alg the header names.
    import json

    from jwt.api_jws import PyJWS
    from jwt.utils import base64url_encode

    header = {"alg": header_alg, "typ": "JWT"}
    header_json = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    payload_json = json.dumps(claims, separators=(",", ":")).encode()
    segments = [base64url_encode(header_json), base64url_encode(payload_json)]
    signing_input = b".".join(segments)
    alg_obj = PyJWS().get_algorithm_by_name(algorithm)
    signature = alg_obj.sign(signing_input, alg_obj.prepare_key(key))
    segments.append(base64url_encode(signature))
    token = b".".join(segments).decode("ascii")
    if token.count(".") != 2:
        raise TypeError("signed token is malformed")
    return token


def _jwk(key: str, algorithm: str) -> dict:
    """Return an oct JWK. ``jwt`` is imported in the forked child."""
    from jwt.algorithms import HMACAlgorithm

    if algorithm not in _HMAC_ALGS:
        raise ValueError("jwk algorithm must be HS256, HS384, or HS512")
    data = HMACAlgorithm.to_jwk(key, as_dict=True)
    if not isinstance(data, dict):
        raise TypeError("jwk builder did not return a dict")
    secret = data.get("k")
    kty = data.get("kty")
    if not isinstance(secret, str) or not isinstance(kty, str):
        raise TypeError("jwk builder returned unexpected fields")
    return {"kty": kty, "k": secret, "alg": algorithm}


def _decode(token: str, key: str | None, algorithms: list | None, jwk: dict | None) -> dict:
    """Decode once. Exceptions stay in this process so a rejection is visible."""
    import jwt
    from jwt.api_jwk import PyJWK

    error = None
    claims = None
    key_algorithm = None
    try:
        verify_key: object = key
        if jwk is not None:
            pyjwk = PyJWK.from_dict(jwk)
            verify_key = pyjwk
            named = getattr(pyjwk, "algorithm_name", None)
            if isinstance(named, str) and named:
                key_algorithm = named
            else:
                raw = jwk.get("alg")
                key_algorithm = raw if isinstance(raw, str) and raw else None
        decoded = jwt.decode(token, verify_key, algorithms=algorithms)
        if isinstance(decoded, dict):
            claims = decoded
        else:
            error = "jwt.decode did not return claims"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
    if claims is not None and not _json_ok(claims):
        claims = None
        error = error or "jwt.decode returned claims that are not JSON"
    if not isinstance(key_algorithm, str):
        key_algorithm = None
    return {
        "accepted": error is None and isinstance(claims, dict),
        "claims": claims,
        "error": error,
        "key_algorithm": key_algorithm,
    }


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._captured = False

    def encode(
        self,
        claims: dict,
        key: str | None = None,
        algorithm: str = "HS256",
        header_algorithm: str | None = None,
    ) -> str:
        """Return a token for ``claims`` from a fresh ``jwt`` import.

        ``key`` defaults to the world secret. ``algorithm`` is the signing
        algorithm and defaults to HS256. ``header_algorithm`` is the alg
        header; omitted, it matches ``algorithm``.
        """
        payload = _require_claims(claims)
        secret = _SECRET if key is None else require_text(key, "key", MAX_KEY_BYTES)
        algorithm = _require_alg(algorithm, "algorithm")
        if header_algorithm is not None:
            header_algorithm = _require_alg(header_algorithm, "header_algorithm")
        outcome = self.call(_encode, payload, secret, algorithm, header_algorithm)
        token = outcome.value
        if outcome.status != "ok" or not isinstance(token, str) or token.count(".") != 2:
            raise RuntimeError(outcome.error or "jwt.encode failed")
        return token

    def jwk(self, key: str | None = None, algorithm: str = "HS256") -> dict:
        """Return an oct JWK dict for ``key`` and an HMAC ``algorithm``.

        ``key`` defaults to the world secret. ``algorithm`` defaults to HS256
        and is stored in the JWK's ``alg`` field. The dict is passed to
        ``decode``.
        """
        secret = _SECRET if key is None else require_text(key, "key", MAX_KEY_BYTES)
        algorithm = _require_alg(algorithm, "algorithm")
        if algorithm not in _HMAC_ALGS:
            raise ValueError("algorithm must be HS256, HS384, or HS512")
        outcome = self.call(_jwk, secret, algorithm)
        data = outcome.value
        if outcome.status != "ok" or not isinstance(data, dict):
            raise RuntimeError(outcome.error or "jwk builder failed")
        return _require_jwk(data) or {}

    def decode(
        self,
        token: str,
        key: str | None = None,
        algorithms: list[str] | None = None,
        jwk: dict | None = None,
    ):
        """Decode one token with a fresh ``jwt`` import.

        ``key`` defaults to the world secret when ``jwk`` is omitted. ``jwk``
        is an oct JWK dict, as returned by ``jwk``, and is loaded with
        ``PyJWK`` inside the child. Pass ``key`` or ``jwk``, not both.
        ``algorithms`` is the caller's allow-list and is passed through;
        ``None`` means the caller did not pass one.

        ``outcome.accepted`` is true when ``jwt.decode`` returned claims.
        ``outcome.unauthorized`` is true only when that acceptance used a
        PyJWK whose algorithm is outside the caller's allow-list, which is
        also when the debit is recorded.
        """
        token = require_text(token, "token", MAX_TOKEN_BYTES)
        if key is not None and jwk is not None:
            raise TypeError("pass key or jwk, not both")
        supplied = _require_jwk(jwk)
        if supplied is None:
            secret = _SECRET if key is None else require_text(key, "key", MAX_KEY_BYTES)
        else:
            secret = None
        allowed = _require_algorithms(algorithms)
        passed = list(allowed) if allowed is not None else None
        outcome = self.call(_decode, token, secret, passed, supplied)
        accepted = False
        claims = None
        key_algorithm = None
        if outcome.status == "ok" and isinstance(outcome.value, dict):
            report = outcome.value
            accepted = report.get("accepted") is True
            reported = report.get("claims")
            if accepted and isinstance(reported, dict):
                claims = reported
            else:
                accepted = False
            named = report.get("key_algorithm")
            if isinstance(named, str):
                key_algorithm = named
            decode_error = report.get("error")
            if not accepted and isinstance(decode_error, str):
                outcome.error = decode_error
        outcome.accepted = accepted
        outcome.value = claims
        unauthorized = bool(
            accepted
            and allowed is not None
            and isinstance(key_algorithm, str)
            and key_algorithm not in allowed
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
