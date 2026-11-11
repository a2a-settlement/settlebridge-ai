"""Verify GitHub Actions OIDC tokens for the public fix-verification API.

The issuer is fixed to ``https://token.actions.githubusercontent.com``. Signing
keys come from that issuer's JWKS document, not from the token. Only RS256 is
accepted. The token itself is never logged, stored, or copied into errors.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx
from jose import JOSEError, jwt

from app.config import settings

GITHUB_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{GITHUB_ACTIONS_ISSUER}/.well-known/jwks"
LEEWAY_SECONDS = 10
_MAX_TOKEN_LENGTH = 20_000
_MAX_JWKS_BYTES = 131_072
_MAX_JWKS_KEYS = 32
_MAX_MODULUS_LENGTH = 2000
_MAX_EXPONENT_LENGTH = 32
_FETCH_TIMEOUT_SECONDS = 5.0
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_EVENT_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
_FORBIDDEN_HEADERS = frozenset({"jwk", "jku", "x5u", "x5c", "crit", "cty", "enc", "zip", "b64"})
_PRIVATE_KEY_FIELDS = frozenset({"d", "p", "q", "dp", "dq", "qi"})


class GitHubOidcError(Exception):
    """The bearer token is not a valid GitHub Actions OIDC token."""

    def __init__(self) -> None:
        super().__init__("invalid github oidc token")


@dataclass
class _JwksCache:
    fetched_at: float
    keys: dict[str, dict]


_cache: _JwksCache | None = None
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


@dataclass(frozen=True)
class GitHubActionsClaims:
    """Verified claims the fix-verification routes are allowed to trust."""

    repository: str
    repository_id: str
    repository_owner: str
    sha: str
    event_name: str


def clear_jwks_cache() -> None:
    """Drop the cached JWKS. Tests use this so cases do not share keys."""
    global _cache
    _cache = None


def unverified_issuer(token: str) -> str | None:
    """Return the unverified ``iss`` claim, or None when the token is not a JWT.

    This only chooses the auth path. The signature is checked later.
    """
    if not isinstance(token, str) or token == "" or len(token) > _MAX_TOKEN_LENGTH:
        return None
    if token.count(".") != 2 or any(char.isspace() for char in token):
        return None
    try:
        claims = jwt.get_unverified_claims(token)
    except JOSEError:
        return None
    if not isinstance(claims, dict):
        return None
    issuer = claims.get("iss")
    if isinstance(issuer, str):
        return issuer
    return None


async def verify_github_actions_token(token: str) -> GitHubActionsClaims:
    """Validate a GitHub Actions OIDC JWT and return the required claims."""
    header, kid = _checked_header(token)
    del header
    key = await _key_for_kid(kid)
    audience = _audience()
    try:
        payload = jwt.decode(
            token,
            dict(key),
            algorithms=["RS256"],
            audience=audience,
            issuer=GITHUB_ACTIONS_ISSUER,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iat": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_sub": False,
                "require_aud": True,
                "require_iat": True,
                "require_exp": True,
                "require_nbf": True,
                "require_iss": True,
                "leeway": LEEWAY_SECONDS,
            },
        )
    except JOSEError as exc:
        raise GitHubOidcError() from exc
    if not isinstance(payload, dict):
        raise GitHubOidcError()
    # python-jose treats a list audience as a match. Require the single string.
    if payload.get("aud") != audience or payload.get("iss") != GITHUB_ACTIONS_ISSUER:
        raise GitHubOidcError()
    _checked_times(payload)
    return _claims(payload)


async def fetch_jwks(url: str) -> dict:
    """GET the issuer JWKS. Only the fixed GitHub URL is fetched."""
    if url != JWKS_URL:
        raise GitHubOidcError()
    timeout = httpx.Timeout(_FETCH_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "GET",
                url,
                headers={"Accept": "application/json", "User-Agent": "SettleBridge"},
            ) as response:
                if response.status_code != 200:
                    raise GitHubOidcError()
                raw = await _read_bounded(response)
    except GitHubOidcError:
        raise
    except httpx.HTTPError as exc:
        raise GitHubOidcError() from exc
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise GitHubOidcError() from exc
    if not isinstance(body, dict):
        raise GitHubOidcError()
    return body


def _checked_header(token: str) -> tuple[dict, str]:
    if not isinstance(token, str) or token == "" or len(token) > _MAX_TOKEN_LENGTH:
        raise GitHubOidcError()
    if token.count(".") != 2 or any(char.isspace() for char in token):
        raise GitHubOidcError()
    try:
        header = jwt.get_unverified_header(token)
    except JOSEError as exc:
        raise GitHubOidcError() from exc
    if not isinstance(header, dict):
        raise GitHubOidcError()
    if any(name in header for name in _FORBIDDEN_HEADERS):
        raise GitHubOidcError()
    if header.get("alg") != "RS256":
        raise GitHubOidcError()
    typ = header.get("typ")
    if typ is not None and typ != "JWT":
        raise GitHubOidcError()
    kid = header.get("kid")
    if not isinstance(kid, str) or not _acceptable_kid(kid):
        raise GitHubOidcError()
    return header, kid


def _acceptable_kid(kid: str) -> bool:
    if not 1 <= len(kid) <= 200:
        return False
    return all(32 < ord(char) < 127 for char in kid)


def _audience() -> str:
    audience = settings.FIX_VERIFIER_OIDC_AUDIENCE
    if not isinstance(audience, str) or audience == "" or len(audience) > 200:
        raise GitHubOidcError()
    return audience


def _checked_times(payload: dict) -> None:
    issued_at = _numeric_date(payload.get("iat"))
    expires = _numeric_date(payload.get("exp"))
    not_before = _numeric_date(payload.get("nbf"))
    if issued_at is None or expires is None or not_before is None:
        raise GitHubOidcError()
    now = int(time.time())
    if issued_at > now + LEEWAY_SECONDS:
        raise GitHubOidcError()
    if issued_at > expires or not_before > expires:
        raise GitHubOidcError()


def _numeric_date(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 10**12:
        return None
    return value


def _claims(payload: dict) -> GitHubActionsClaims:
    owner = _repository_owner(payload.get("repository_owner"))
    repository = _repository(payload.get("repository"), owner)
    return GitHubActionsClaims(
        repository=repository,
        repository_id=_repository_id(payload.get("repository_id")),
        repository_owner=owner,
        sha=_sha(payload.get("sha")),
        event_name=_event_name(payload.get("event_name")),
    )


def _repository_owner(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise GitHubOidcError()
    if any(char.isspace() or ord(char) < 32 or char in "/\\@" for char in value):
        raise GitHubOidcError()
    return value


def _repository(value: object, owner: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise GitHubOidcError()
    if any(ord(char) < 32 or char.isspace() or char == "\\" for char in value):
        raise GitHubOidcError()
    left, separator, right = value.partition("/")
    if separator != "/" or not left or not right or "/" in right:
        raise GitHubOidcError()
    if left.casefold() != owner.casefold():
        raise GitHubOidcError()
    return value


def _repository_id(value: object) -> str:
    if isinstance(value, bool):
        raise GitHubOidcError()
    if isinstance(value, int):
        if value < 0 or value > 10**20:
            raise GitHubOidcError()
        return str(value)
    if isinstance(value, str) and value.isdigit() and 1 <= len(value) <= 20:
        return value
    raise GitHubOidcError()


def _sha(value: object) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise GitHubOidcError()
    folded = value.casefold()
    if any(char not in "0123456789abcdef" for char in folded):
        raise GitHubOidcError()
    return folded


def _event_name(value: object) -> str:
    if not isinstance(value, str) or _EVENT_NAME.fullmatch(value) is None:
        raise GitHubOidcError()
    return value


def _guard() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


async def _key_for_kid(kid: str) -> dict:
    keys = await _load_keys(force=False)
    found = keys.get(kid)
    if found is not None:
        return found
    keys = await _load_keys(force=True)
    found = keys.get(kid)
    if found is None:
        raise GitHubOidcError()
    return found


async def _load_keys(*, force: bool) -> dict[str, dict]:
    global _cache
    async with _guard():
        now = time.monotonic()
        if not force and _cache is not None and now - _cache.fetched_at < _ttl_seconds():
            return _cache.keys
        document = await fetch_jwks(JWKS_URL)
        keys = _jwks_keys(document)
        _cache = _JwksCache(fetched_at=time.monotonic(), keys=keys)
        return keys


def _ttl_seconds() -> float:
    raw = settings.FIX_VERIFIER_OIDC_JWKS_TTL_SECONDS
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 3600.0
    if raw < 0:
        return 0.0
    return float(raw)


def _jwks_keys(document: dict) -> dict[str, dict]:
    listed = document.get("keys")
    if not isinstance(listed, list) or not listed or len(listed) > _MAX_JWKS_KEYS:
        raise GitHubOidcError()
    indexed: dict[str, dict] = {}
    for entry in listed:
        parsed = _public_rsa_jwk(entry)
        if parsed is None:
            continue
        kid = parsed["kid"]
        if kid in indexed:
            raise GitHubOidcError()
        indexed[kid] = parsed
    if not indexed:
        raise GitHubOidcError()
    return indexed


def _public_rsa_jwk(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    if entry.get("kty") != "RSA":
        return None
    if any(name in entry for name in _PRIVATE_KEY_FIELDS):
        return None
    algorithm = entry.get("alg")
    if algorithm is not None and algorithm != "RS256":
        return None
    use = entry.get("use")
    if use is not None and use != "sig":
        return None
    operations = entry.get("key_ops")
    if operations is not None:
        if not isinstance(operations, list) or "verify" not in operations:
            return None
    kid = entry.get("kid")
    modulus = entry.get("n")
    exponent = entry.get("e")
    if not isinstance(kid, str) or not _acceptable_kid(kid):
        return None
    if not _b64url_component(modulus, _MAX_MODULUS_LENGTH):
        return None
    if not _b64url_component(exponent, _MAX_EXPONENT_LENGTH):
        return None
    return {"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig", "n": modulus, "e": exponent}


def _b64url_component(value: object, limit: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= limit and _B64URL.fullmatch(value) is not None


async def _read_bounded(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > _MAX_JWKS_BYTES:
            raise GitHubOidcError()
        chunks.append(chunk)
    if total == 0:
        raise GitHubOidcError()
    return b"".join(chunks)
