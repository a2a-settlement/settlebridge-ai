"""Verifier world for pyjwt-options-mutation (integrity).

Property: ``jwt.decode`` never changes the ``options`` dict a caller passes in,
so a later call with the same dict checks every claim it asks for.

The 2.11.0 regression removed the defensive ``dict(options or {})`` copy.
``_merge_options`` writes into the caller's dict when ``verify_signature`` is
false, assigning ``verify_exp`` (and ``verify_nbf``, ``verify_iat``,
``verify_aud``, ``verify_iss``, ``verify_sub``, ``verify_jti``) to false.
A later decode that turns signature verification back on still skips the
expiration check and accepts an expired token.

``encode`` and ``decode`` are single library calls. ``options_id`` keeps one
options dict on the world and passes that same object into later calls. Keys
in a later ``options`` argument are assigned on that object; keys left from an
earlier call stay. The world reads ``exp`` off the token itself. It records an
unauthorized account debit only when a decode accepts a token that is already
expired and this call's caller asked for an expiration check. A fresh token,
or a caller who turned that check off, writes nothing.
"""

from __future__ import annotations

import base64
import json
import time

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

_SECRET = "sbc-fixed-secret"
_DEFAULT_ALGORITHMS = ["HS256"]
MAX_TOKEN_BYTES = 64 * 1024
MAX_CLAIMS_BYTES = 16 * 1024
MAX_KEY_BYTES = 8 * 1024
MAX_ALG_BYTES = 64
MAX_ID_BYTES = 128
MAX_OPTIONS = 32
MAX_ALGORITHMS = 16
_OPTION_TEXT = 1024


def _json_ok(value: object) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def _require_claims(claims: object) -> dict:
    if type(claims) is not dict:
        raise TypeError("claims must be a plain dict")
    if len(claims) > MAX_OPTIONS:
        raise ValueError(f"claims has more than {MAX_OPTIONS} keys")
    try:
        raw = json.dumps(claims).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("claims must be JSON-serializable") from exc
    if len(raw) > MAX_CLAIMS_BYTES:
        raise ValueError(f"claims is longer than {MAX_CLAIMS_BYTES} bytes")
    return claims


def _option_value(value: object, name: str) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if type(value) is str:
        return require_text(value, name, _OPTION_TEXT)
    if type(value) in (int, float):
        return value
    if type(value) is list:
        if len(value) > MAX_OPTIONS:
            raise ValueError(f"{name} has more than {MAX_OPTIONS} items")
        return [_option_value(item, name) for item in value]
    raise TypeError(f"{name} must be a JSON value")


def _require_options(options: object) -> dict | None:
    if options is None:
        return None
    if type(options) is not dict:
        raise TypeError("options must be a plain dict")
    if len(options) > MAX_OPTIONS:
        raise ValueError(f"options has more than {MAX_OPTIONS} keys")
    checked: dict = {}
    for index, (key, value) in enumerate(options.items()):
        name = require_text(key, f"options key {index}", MAX_ALG_BYTES)
        checked[name] = _option_value(value, name)
    return checked


def _require_algorithms(algorithms: object) -> list[str]:
    if algorithms is None:
        return list(_DEFAULT_ALGORITHMS)
    if type(algorithms) is not list or not algorithms or len(algorithms) > MAX_ALGORITHMS:
        raise TypeError("algorithms must be a short non-empty list of str")
    return [require_text(item, "algorithms item", MAX_ALG_BYTES) for item in algorithms]


def _payload(token: str) -> dict | None:
    """JWT payload from the token text. No signature check and no library call."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    pad = "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(parts[1] + pad)
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if type(payload) is not dict:
        return None
    return payload


def _token_expired(token: str) -> bool:
    """True when the token's own ``exp`` is already due.

    Matches PyJWT with the default leeway of zero: ``int(exp) <= now``.
    A token whose ``exp`` cannot be read is not treated as expired.
    """
    payload = _payload(token)
    if payload is None or "exp" not in payload:
        return False
    exp = payload["exp"]
    if isinstance(exp, bool) or not isinstance(exp, (int, float, str)):
        return False
    try:
        exp_value = int(exp)
    except (TypeError, ValueError, OverflowError):
        return False
    return exp_value <= time.time()


def _caller_asked_for_exp(options: dict | None, explicit: set[str]) -> bool:
    """True when this call's caller asked for an expiration check.

    PyJWT checks ``exp`` unless the caller turns signature verification off or
    sets ``verify_exp`` false. Only keys the caller passed in count. A flag
    the library wrote into a reused dict is not a request.
    """
    if options is None:
        return True
    signature_off = "verify_signature" in explicit and not bool(
        options.get("verify_signature", True)
    )
    if signature_off:
        return "verify_exp" in explicit and bool(options.get("verify_exp"))
    if "verify_exp" in explicit and not bool(options.get("verify_exp")):
        return False
    return True


def _encode(claims: dict, key: str, algorithm: str) -> str:
    """Build one token. ``jwt`` is imported in the forked child."""
    import jwt

    token = jwt.encode(claims, key, algorithm=algorithm)
    if isinstance(token, bytes):
        token = token.decode("ascii")
    if not isinstance(token, str) or not token:
        raise TypeError("jwt.encode did not return a token string")
    return token


def _decode(token: str, key: str, algorithms: list, options: dict | None) -> dict:
    """Decode once and return the options dict as the library left it.

    Exceptions stay in this process so a mutation that happens before a
    rejection is still visible to the parent.
    """
    import jwt

    error = None
    claims = None
    try:
        decoded = jwt.decode(token, key, algorithms=algorithms, options=options)
        if isinstance(decoded, dict):
            claims = decoded
        else:
            error = "jwt.decode did not return claims"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
    reported = dict(options) if isinstance(options, dict) else None
    if claims is not None and not _json_ok(claims):
        claims = None
    if reported is not None and not _json_ok(reported):
        reported = None
    return {
        "accepted": error is None,
        "claims": claims,
        "error": error,
        "options": reported,
    }


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._options: dict[str, dict] = {}
        self._explicit: dict[str, set[str]] = {}
        self._captured = False

    def _bind(self, supplied: dict | None, options_id: str | None) -> tuple[dict | None, set[str]]:
        """Return the dict ``jwt.decode`` will see, and the caller's own keys.

        With no ``options_id`` the dict is used for this call only. With an id
        the world keeps one dict object and copies library writes back onto it.
        """
        if options_id is None:
            if supplied is None:
                return None, set()
            return dict(supplied), set(supplied)
        store = self._options.get(options_id)
        if store is None:
            if len(self._options) >= MAX_OPTIONS:
                raise ValueError(f"more than {MAX_OPTIONS} options ids")
            store = {} if supplied is None else dict(supplied)
            self._options[options_id] = store
            self._explicit[options_id] = set(store)
            return store, set(self._explicit[options_id])
        if supplied:
            for key, value in supplied.items():
                store[key] = value
                self._explicit[options_id].add(key)
        return store, set(self._explicit[options_id])

    def _write_back(self, options_id: str, returned: object) -> None:
        """Copy library writes onto the stored dict. Same object, new contents."""
        store = self._options.get(options_id)
        if store is None or not isinstance(returned, dict):
            return
        store.clear()
        for key, value in returned.items():
            if isinstance(key, str):
                store[key] = value

    def encode(self, claims: dict, key: str | None = None, algorithm: str = "HS256") -> str:
        """Return a token for ``claims`` from a fresh ``jwt`` import.

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
        options: dict | None = None,
        options_id: str | None = None,
    ):
        """Decode one token with a fresh ``jwt`` import.

        ``key`` defaults to the world secret and ``algorithms`` to ``["HS256"]``.
        ``options_id`` reuses one stored options dict across calls. Keys in
        ``options`` are assigned onto that dict; every other key already there
        stays, including keys an earlier decode wrote. ``outcome.accepted`` is
        true when ``jwt.decode`` returned claims. ``outcome.unauthorized`` is
        true only when that acceptance is an expired token whose caller asked
        for an expiration check, which is also when the debit is recorded.
        """
        token = require_text(token, "token", MAX_TOKEN_BYTES)
        secret = _SECRET if key is None else require_text(key, "key", MAX_KEY_BYTES)
        algs = _require_algorithms(algorithms)
        supplied = _require_options(options)
        if options_id is not None:
            options_id = require_text(options_id, "options_id", MAX_ID_BYTES)
        bound, explicit = self._bind(supplied, options_id)
        asked = _caller_asked_for_exp(bound, explicit)
        expired = _token_expired(token)
        outcome = self.call(_decode, token, secret, algs, bound)
        accepted = False
        claims = None
        returned = None
        if outcome.status == "ok" and isinstance(outcome.value, dict):
            report = outcome.value
            accepted = report.get("accepted") is True
            claims = report.get("claims")
            returned = report.get("options")
            decode_error = report.get("error")
            if not accepted and isinstance(decode_error, str):
                outcome.error = decode_error
        if options_id is not None:
            self._write_back(options_id, returned)
        outcome.accepted = accepted
        outcome.value = claims if accepted and isinstance(claims, dict) else None
        unauthorized = bool(accepted and asked and expired)
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
