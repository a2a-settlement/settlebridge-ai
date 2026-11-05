"""GitHub App client tests. No network — httpx.MockTransport only."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.services import github_app  # noqa: E402
from app.services.github_app import GitHubAPIError  # noqa: E402

FROZEN = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
API = "https://api.github.com"
REPO = "acme/widgets"


def _rsa_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public.decode("ascii")


_PRIVATE_PEM, _PUBLIC_PEM = _rsa_pair()


@pytest.fixture(autouse=True)
def _clear_token_cache():
    github_app._token_cache.clear()
    yield
    github_app._token_cache.clear()


def _configure(monkeypatch: pytest.MonkeyPatch, private_key: str, app_id: str = "8675309") -> None:
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_ID", app_id)
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_PRIVATE_KEY", private_key)
    monkeypatch.setattr(github_app.settings, "GITHUB_API_URL", API)
    monkeypatch.setattr(github_app, "_now", lambda: FROZEN)


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=30)

    monkeypatch.setattr(github_app, "_client", factory)


def _refuse_network() -> httpx.AsyncClient:
    raise AssertionError("HTTP client should not be constructed")


def _expiry(delta: timedelta) -> str:
    return (FROZEN + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_json(request: httpx.Request, expires_at: str) -> httpx.Response:
    installation_id = request.url.path.split("/")[3]
    return httpx.Response(
        201,
        json={"token": f"ghs_{installation_id}", "expires_at": expires_at},
    )


def _decode(token: str) -> dict:
    return jwt.decode(token, _PUBLIC_PEM, algorithms=["RS256"], options={"verify_exp": False})


def _app_jwt_from(request: httpx.Request) -> str:
    scheme, token = request.headers["authorization"].split(" ", 1)
    assert scheme == "Bearer"
    return token


def test_missing_config_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github_app, "_client", _refuse_network)
    cases = [("", _PRIVATE_PEM), ("8675309", ""), (" ", " ")]
    for app_id, key in cases:
        monkeypatch.setattr(github_app.settings, "GITHUB_APP_ID", app_id)
        monkeypatch.setattr(github_app.settings, "GITHUB_APP_PRIVATE_KEY", key)
        with pytest.raises(GitHubAPIError, match="GitHub App not configured"):
            asyncio.run(github_app.installation_token(1))


def test_app_jwt_claims_from_inline_pem(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _token_json(request, _expiry(timedelta(hours=1)))

    _install(monkeypatch, handler)
    minted = asyncio.run(github_app.installation_token(42))
    assert minted == "ghs_42"
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/app/installations/42/access_tokens"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    app_jwt = _app_jwt_from(request)
    assert jwt.get_unverified_header(app_jwt)["alg"] == "RS256"
    claims = _decode(app_jwt)
    issued = int(FROZEN.timestamp())
    assert claims["iat"] == issued - 60
    assert claims["exp"] == issued + 540
    assert claims["iss"] == "8675309"


def test_app_jwt_accepts_pem_path(monkeypatch: pytest.MonkeyPatch, tmp_path):
    path = tmp_path / "github-app.pem"
    path.write_text(_PRIVATE_PEM, encoding="utf-8")
    _configure(monkeypatch, str(path))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _token_json(request, _expiry(timedelta(hours=1)))

    _install(monkeypatch, handler)
    asyncio.run(github_app.installation_token(7))
    claims = _decode(_app_jwt_from(seen[0]))
    assert claims["iss"] == "8675309"
    assert claims["exp"] - claims["iat"] == 600


def test_app_jwt_accepts_escaped_newlines(monkeypatch: pytest.MonkeyPatch):
    escaped = _PRIVATE_PEM.replace("\n", "\\n")
    assert "\n" not in escaped
    assert "\\n" in escaped
    _configure(monkeypatch, escaped)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _token_json(request, _expiry(timedelta(hours=1)))

    _install(monkeypatch, handler)
    asyncio.run(github_app.installation_token(7))
    claims = _decode(_app_jwt_from(seen[0]))
    assert claims["iat"] == int(FROZEN.timestamp()) - 60


def test_installation_token_is_cached(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, _PRIVATE_PEM)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        posts["n"] += 1
        return _token_json(request, _expiry(timedelta(hours=1)))

    _install(monkeypatch, handler)

    async def scenario() -> tuple[str, str, str]:
        first = await github_app.installation_token(11)
        other = await github_app.installation_token(22)
        again = await github_app.installation_token(11)
        return first, other, again

    assert asyncio.run(scenario()) == ("ghs_11", "ghs_22", "ghs_11")
    assert posts["n"] == 2


def test_installation_token_refreshes_inside_skew(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, _PRIVATE_PEM)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        posts["n"] += 1
        return _token_json(request, _expiry(timedelta(minutes=5)))

    _install(monkeypatch, handler)

    async def scenario() -> None:
        await github_app.installation_token(11)
        await github_app.installation_token(11)

    asyncio.run(scenario())
    assert posts["n"] == 2


def test_get_pull_request_returns_json(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        return httpx.Response(200, json={"number": 12, "title": "Fix"})

    _install(monkeypatch, handler)
    payload = asyncio.run(github_app.get_pull_request(5, REPO, 12))
    assert payload == {"number": 12, "title": "Fix"}
    pull = next(request for request in seen if request.method == "GET")
    assert pull.url.path == "/repos/acme/widgets/pulls/12"
    assert pull.headers["accept"] == "application/vnd.github+json"
    assert pull.headers["x-github-api-version"] == "2022-11-28"
    assert pull.headers["authorization"] == "Bearer ghs_5"


def test_compare_diff_requests_diff_media_type(monkeypatch: pytest.MonkeyPatch):
    base, head = "a" * 40, "b" * 40
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        return httpx.Response(200, text="diff --git a/f b/f\n")

    _install(monkeypatch, handler)
    text = asyncio.run(github_app.compare_diff(5, REPO, base, head))
    assert text.startswith("diff --git")
    compare = seen[-1]
    assert compare.method == "GET"
    assert compare.url.path == f"/repos/acme/widgets/compare/{base}...{head}"
    assert compare.headers["accept"] == "application/vnd.github.diff"
    assert compare.headers["x-github-api-version"] == "2022-11-28"
    assert compare.headers["authorization"] == "Bearer ghs_5"


def _check_run(page: int, sha: str) -> dict:
    return {
        "name": f"check-{page}",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": page, "slug": f"app-{page}"},
    }


def test_list_check_runs_follows_next_rel_only(monkeypatch: pytest.MonkeyPatch):
    sha = "c" * 40
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        page = int(request.url.params.get("page", "1"))
        assert request.url.params["per_page"] == "100"
        assert request.headers["accept"] == "application/vnd.github+json"
        headers: dict[str, str] = {}
        if page == 1:
            stem = f"{API}/repos/acme/widgets/commits/{sha}/check-runs?per_page=100&page="
            headers["Link"] = (
                f'<{stem}0>; rel="prev", <{stem}2>; rel="next", <{stem}9>; rel="last"'
            )
        return httpx.Response(200, json={"check_runs": [_check_run(page, sha)]}, headers=headers)

    _install(monkeypatch, handler)
    runs = asyncio.run(github_app.list_check_runs(5, REPO, sha))
    assert [run["name"] for run in runs] == ["check-1", "check-2"]
    assert runs[1]["app"] == {"id": 2, "slug": "app-2"}
    pages = [request.url.params.get("page") for request in seen if "check-runs" in request.url.path]
    assert pages == [None, "2"]


def test_list_check_runs_stops_at_ten_pages(monkeypatch: pytest.MonkeyPatch):
    sha = "d" * 40
    _configure(monkeypatch, _PRIVATE_PEM)
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        page = int(request.url.params.get("page", "1"))
        pages.append(page)
        nxt = (
            f"{API}/repos/acme/widgets/commits/{sha}/check-runs?per_page=100&page={page + 1}"
        )
        headers = {"Link": f'<{nxt}>; rel="next"'}
        return httpx.Response(200, json={"check_runs": [_check_run(page, sha)]}, headers=headers)

    _install(monkeypatch, handler)
    runs = asyncio.run(github_app.list_check_runs(5, REPO, sha))
    assert pages == list(range(1, 11))
    assert len(runs) == 10
    assert runs[-1]["conclusion"] == "success"


def test_upsert_updates_comment_by_id(monkeypatch: pytest.MonkeyPatch):
    marker = "<!-- settlebridge:bounty:abc -->"
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        assert request.method == "PATCH"
        assert request.url.path == "/repos/acme/widgets/issues/comments/55"
        posted = json.loads(request.content)["body"]
        assert posted.startswith(marker + "\n")
        assert posted.endswith("hello")
        return httpx.Response(200, json={"id": 55})

    _install(monkeypatch, handler)
    result = asyncio.run(
        github_app.upsert_sticky_comment(3, REPO, 7, marker, "hello", comment_id=55)
    )
    assert result == 55
    assert [request.method for request in seen] == ["POST", "PATCH"]


def test_upsert_patches_comment_found_by_marker(monkeypatch: pytest.MonkeyPatch):
    marker = "<!-- settlebridge:bounty:abc -->"
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        if request.method == "GET":
            page = request.url.params.get("page", "1")
            assert request.url.params["per_page"] == "100"
            if page == "1":
                nxt = f"{API}/repos/acme/widgets/issues/7/comments?per_page=100&page=2"
                return httpx.Response(
                    200,
                    json=[{"id": 1, "body": "unrelated"}],
                    headers={"Link": f'<{nxt}>; rel="next"'},
                )
            return httpx.Response(200, json=[{"id": 8, "body": f"note {marker} tail"}])
        assert request.url.path == "/repos/acme/widgets/issues/comments/8"
        assert marker in json.loads(request.content)["body"]
        return httpx.Response(200, json={"id": 8})

    _install(monkeypatch, handler)
    result = asyncio.run(github_app.upsert_sticky_comment(3, REPO, 7, marker, "updated"))
    assert result == 8
    assert [request.method for request in seen] == ["POST", "GET", "GET", "PATCH"]


def test_upsert_creates_when_marker_is_absent(monkeypatch: pytest.MonkeyPatch):
    marker = "<!-- settlebridge:bounty:abc -->"
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 3, "body": "no marker here"}])
        assert request.method == "POST"
        assert request.url.path == "/repos/acme/widgets/issues/7/comments"
        assert marker in json.loads(request.content)["body"]
        return httpx.Response(201, json={"id": 9})

    _install(monkeypatch, handler)
    result = asyncio.run(github_app.upsert_sticky_comment(3, REPO, 7, marker, "fresh"))
    assert result == 9
    assert [request.method for request in seen] == ["POST", "GET", "POST"]


def test_upsert_404_on_id_falls_through_to_marker(monkeypatch: pytest.MonkeyPatch):
    marker = "<!-- settlebridge:bounty:abc -->"
    _configure(monkeypatch, _PRIVATE_PEM)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        if request.method == "PATCH" and request.url.path.endswith("/comments/55"):
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 4, "body": marker + "\nold"}])
        assert request.url.path.endswith("/comments/4")
        return httpx.Response(200, json={"id": 4})

    _install(monkeypatch, handler)
    result = asyncio.run(
        github_app.upsert_sticky_comment(3, REPO, 7, marker, marker + "\nfresh", comment_id=55)
    )
    assert result == 4
    assert [request.method for request in seen] == ["POST", "PATCH", "GET", "PATCH"]


def test_upsert_patch_error_does_not_fall_through(monkeypatch: pytest.MonkeyPatch):
    marker = "<!-- settlebridge:bounty:abc -->"
    _configure(monkeypatch, _PRIVATE_PEM)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        if request.method == "PATCH":
            return httpx.Response(500, json={"message": "nope"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    _install(monkeypatch, handler)
    with pytest.raises(GitHubAPIError) as caught:
        asyncio.run(
            github_app.upsert_sticky_comment(3, REPO, 7, marker, "hello", comment_id=55)
        )
    assert caught.value.status_code == 500


def test_permission_404_is_none(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, _PRIVATE_PEM)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        assert request.url.path == "/repos/acme/widgets/collaborators/octocat/permission"
        return httpx.Response(404, json={"message": "Not Found"})

    _install(monkeypatch, handler)
    result = asyncio.run(github_app.get_collaborator_permission(1, REPO, "octocat"))
    assert result == "none"


def test_permission_maintain_role_and_admin(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, _PRIVATE_PEM)
    payloads = {
        "maintainer": {"permission": "write", "role_name": "maintain"},
        "octocat": {"permission": "admin", "role_name": "admin"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        username = request.url.path.split("/")[5]
        return httpx.Response(200, json=payloads[username])

    _install(monkeypatch, handler)
    maintain = asyncio.run(github_app.get_collaborator_permission(1, REPO, "maintainer"))
    admin = asyncio.run(github_app.get_collaborator_permission(1, REPO, "octocat"))
    assert maintain == "maintain"
    assert admin == "admin"


def test_error_status_raises_github_api_error(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, _PRIVATE_PEM)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return _token_json(request, _expiry(timedelta(hours=1)))
        return httpx.Response(502, json={"message": "bad gateway"})

    _install(monkeypatch, handler)
    with pytest.raises(GitHubAPIError) as caught:
        asyncio.run(github_app.get_pull_request(1, REPO, 4))
    assert caught.value.status_code == 502
    assert "ghs_" not in str(caught.value)


def test_invalid_repo_and_sha_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github_app, "_client", _refuse_network)
    _configure(monkeypatch, _PRIVATE_PEM)
    sha = "a" * 40
    calls = [
        github_app.get_pull_request(1, "acme/widgets/extra", 1),
        github_app.compare_diff(1, "acme/../x", sha, sha),
        github_app.compare_diff(1, REPO, "ABC1234", sha),
        github_app.compare_diff(1, REPO, "abc123", sha),
        github_app.list_check_runs(1, "not a repo", sha),
        github_app.upsert_sticky_comment(1, "acme/widgets?x=1", 1, "m", "b"),
        github_app.get_collaborator_permission(1, REPO, "octo/cat"),
    ]
    for call in calls:
        with pytest.raises(GitHubAPIError):
            asyncio.run(call)
