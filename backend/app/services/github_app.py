"""GitHub App client: app JWT, installation tokens, diffs, check runs, comments."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from jose import jwt

from app.config import settings

_API_VERSION = "2022-11-28"
_JSON_ACCEPT = "application/vnd.github+json"
_DIFF_ACCEPT = "application/vnd.github.diff"
_MAX_PAGES = 10
_TOKEN_SKEW = timedelta(minutes=5)
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel=("next"|next)(?=[\s,;]|$)', re.IGNORECASE)

# Keyed by installation id. Values are (token, expires_at).
_token_cache: dict[int, tuple[str, datetime]] = {}


class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _api_root() -> str:
    return settings.GITHUB_API_URL.rstrip("/")


def _headers(token: str, *, accept: str = _JSON_ACCEPT) -> dict[str, str]:
    return {
        "Accept": accept,
        "X-GitHub-Api-Version": _API_VERSION,
        "Authorization": f"Bearer {token}",
    }


def _validate_repo(repo: str) -> str:
    if not isinstance(repo, str) or _REPO_RE.fullmatch(repo) is None:
        raise GitHubAPIError("invalid repository")
    return repo


def _validate_sha(sha: str) -> str:
    if not isinstance(sha, str) or _SHA_RE.fullmatch(sha) is None:
        raise GitHubAPIError("invalid commit sha")
    return sha


def _validate_username(username: str) -> str:
    if not isinstance(username, str) or username == "":
        raise GitHubAPIError("invalid username")
    if any(char in username for char in "/\\?#\r\n\t "):
        raise GitHubAPIError("invalid username")
    return quote(username, safe="")


def _load_private_key() -> str:
    app_id = settings.GITHUB_APP_ID
    raw = settings.GITHUB_APP_PRIVATE_KEY
    if app_id is None or raw is None or not str(app_id).strip() or not str(raw).strip():
        raise GitHubAPIError("GitHub App not configured")
    text = str(raw).strip()
    # Env files often store the PEM with literal "\n" escapes rather than real newlines.
    if "-----BEGIN" in text or "\\n" in text:
        return text.replace("\\n", "\n").strip()
    path = Path(text)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as exc:
        raise GitHubAPIError("GitHub App not configured") from exc
    raise GitHubAPIError("GitHub App not configured")


def _app_jwt() -> str:
    key = _load_private_key()
    now = int(_now().timestamp())
    claims = {"iat": now - 60, "exp": now + 540, "iss": settings.GITHUB_APP_ID}
    encoded = jwt.encode(claims, key, algorithm="RS256")
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return encoded


def _parse_expiry(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise GitHubAPIError("GitHub installation token response was incomplete") from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _ensure_ok(response: httpx.Response) -> None:
    if 200 <= response.status_code < 300:
        return
    method = response.request.method
    path = response.request.url.path
    raise GitHubAPIError(
        f"GitHub API {response.status_code} for {method} {path}",
        status_code=response.status_code,
    )


def _read_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        # Drop the decoder error so a token in the body cannot leak via __context__.
        raise GitHubAPIError("GitHub API returned invalid JSON", response.status_code) from None


async def _send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    token: str,
    *,
    accept: str = _JSON_ACCEPT,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    kwargs: dict[str, Any] = {"headers": _headers(token, accept=accept)}
    if json_body is not None:
        kwargs["json"] = json_body
    return await client.request(method, url, **kwargs)


def _next_url(response: httpx.Response) -> str | None:
    header = response.headers.get("link")
    if not header:
        return None
    found: str | None = None
    for part in header.split(","):
        match = _NEXT_LINK.search(part.strip())
        if match:
            found = match.group(1).strip()
            break
    if not found:
        return None
    base = urlparse(settings.GITHUB_API_URL)
    if found.startswith("/"):
        return f"{base.scheme}://{base.netloc}{found}"
    parsed = urlparse(found)
    # Installation tokens must not be sent to a host other than the configured API.
    if parsed.scheme not in {"http", "https"} or parsed.netloc != base.netloc:
        raise GitHubAPIError("refusing to follow GitHub pagination off the API host")
    return found


def _with_marker(marker: str, body: str) -> str:
    if marker in body:
        return body
    if body:
        return f"{marker}\n{body}"
    return marker


def _comment_id(response: httpx.Response) -> int:
    data = _read_json(response)
    if not isinstance(data, dict) or "id" not in data:
        raise GitHubAPIError("GitHub comment response missing id", response.status_code)
    value = data["id"]
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GitHubAPIError("GitHub comment response missing id", response.status_code)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise GitHubAPIError("GitHub comment response missing id", response.status_code) from None


async def installation_token(installation_id: int) -> str:
    cached = _token_cache.get(installation_id)
    now = _now()
    if cached is not None:
        token, expires_at = cached
        # Usable until five minutes before expires_at; then mint a new one.
        if now < expires_at - _TOKEN_SKEW:
            return token
    app_jwt = _app_jwt()
    url = f"{_api_root()}/app/installations/{installation_id}/access_tokens"
    async with _client() as client:
        response = await _send(client, "POST", url, app_jwt)
    _ensure_ok(response)
    data = _read_json(response)
    if not isinstance(data, dict):
        raise GitHubAPIError(
            "GitHub installation token response was incomplete", response.status_code
        )
    token = data.get("token")
    expires_raw = data.get("expires_at")
    if not isinstance(token, str) or not token or not isinstance(expires_raw, str):
        raise GitHubAPIError(
            "GitHub installation token response was incomplete", response.status_code
        )
    _token_cache[installation_id] = (token, _parse_expiry(expires_raw))
    return token


async def get_pull_request(installation_id: int, repo: str, pr_number: int) -> dict:
    repo = _validate_repo(repo)
    token = await installation_token(installation_id)
    url = f"{_api_root()}/repos/{repo}/pulls/{pr_number}"
    async with _client() as client:
        response = await _send(client, "GET", url, token)
    _ensure_ok(response)
    data = _read_json(response)
    if not isinstance(data, dict):
        raise GitHubAPIError("unexpected pull request payload", response.status_code)
    return data


async def compare_diff(installation_id: int, repo: str, base_sha: str, head_sha: str) -> str:
    repo = _validate_repo(repo)
    base = _validate_sha(base_sha)
    head = _validate_sha(head_sha)
    token = await installation_token(installation_id)
    url = f"{_api_root()}/repos/{repo}/compare/{base}...{head}"
    async with _client() as client:
        response = await _send(client, "GET", url, token, accept=_DIFF_ACCEPT)
    _ensure_ok(response)
    return response.text


async def list_check_runs(installation_id: int, repo: str, head_sha: str) -> list[dict]:
    repo = _validate_repo(repo)
    head = _validate_sha(head_sha)
    token = await installation_token(installation_id)
    url = f"{_api_root()}/repos/{repo}/commits/{head}/check-runs?per_page=100"
    runs: list[dict] = []
    async with _client() as client:
        for _ in range(_MAX_PAGES):
            response = await _send(client, "GET", url, token)
            _ensure_ok(response)
            payload = _read_json(response)
            if not isinstance(payload, dict):
                raise GitHubAPIError("unexpected check-runs payload", response.status_code)
            batch = payload.get("check_runs", [])
            if not isinstance(batch, list):
                raise GitHubAPIError("unexpected check-runs payload", response.status_code)
            runs.extend(batch)
            nxt = _next_url(response)
            if nxt is None:
                break
            url = nxt
    return runs


async def _find_comment_id(
    client: httpx.AsyncClient,
    token: str,
    repo: str,
    issue_number: int,
    marker: str,
) -> int | None:
    url = f"{_api_root()}/repos/{repo}/issues/{issue_number}/comments?per_page=100"
    for _ in range(_MAX_PAGES):
        response = await _send(client, "GET", url, token)
        _ensure_ok(response)
        comments = _read_json(response)
        if not isinstance(comments, list):
            raise GitHubAPIError("unexpected comment list", response.status_code)
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            if marker in str(comment.get("body") or ""):
                value = comment.get("id")
                if isinstance(value, bool) or not isinstance(value, (int, str)):
                    raise GitHubAPIError("GitHub comment response missing id", response.status_code)
                return int(value)
        nxt = _next_url(response)
        if nxt is None:
            return None
        url = nxt
    return None


async def upsert_sticky_comment(
    installation_id: int,
    repo: str,
    issue_number: int,
    marker: str,
    body: str,
    comment_id: int | None = None,
) -> int:
    repo = _validate_repo(repo)
    posted = _with_marker(marker, body)
    token = await installation_token(installation_id)
    async with _client() as client:
        if comment_id is not None:
            url = f"{_api_root()}/repos/{repo}/issues/comments/{comment_id}"
            response = await _send(client, "PATCH", url, token, json_body={"body": posted})
            if response.status_code != 404:
                _ensure_ok(response)
                return _comment_id(response)
        found = await _find_comment_id(client, token, repo, issue_number, marker)
        if found is not None:
            url = f"{_api_root()}/repos/{repo}/issues/comments/{found}"
            response = await _send(client, "PATCH", url, token, json_body={"body": posted})
            _ensure_ok(response)
            return _comment_id(response)
        url = f"{_api_root()}/repos/{repo}/issues/{issue_number}/comments"
        response = await _send(client, "POST", url, token, json_body={"body": posted})
        _ensure_ok(response)
        return _comment_id(response)


async def get_collaborator_permission(installation_id: int, repo: str, username: str) -> str:
    repo = _validate_repo(repo)
    safe_user = _validate_username(username)
    token = await installation_token(installation_id)
    url = f"{_api_root()}/repos/{repo}/collaborators/{safe_user}/permission"
    async with _client() as client:
        response = await _send(client, "GET", url, token)
    if response.status_code == 404:
        return "none"
    _ensure_ok(response)
    data = _read_json(response)
    if not isinstance(data, dict):
        raise GitHubAPIError("unexpected permission payload", response.status_code)
    if data.get("role_name") == "maintain":
        return "maintain"
    permission = data.get("permission")
    if isinstance(permission, str) and permission:
        return permission
    return "none"
