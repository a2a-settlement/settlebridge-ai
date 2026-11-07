"""Independent re-derivation: the verifier's own sweep of every read route.

Each unauthorized identity calls every GET route of the real app with path
parameters pointed at tenant B's private bounty, share token, submission and repo,
then tries the claim routes, then repeats the reads after a reader's access is
revoked and the documented 5-minute cache has expired. Nothing is asserted: the
observer decides from what was recorded.
"""

import re

from fastapi.routing import APIRoute

_PARAM = re.compile(r"{([^}:]+)(?::[^}]+)?}")
_UNAUTHORIZED = (None, "outsider", "no_github", "mismatch", "lessee")


def _value(world, name, target="private"):
    key = name.lower()
    owner, repo = world.private_repo.split("/", 1)
    if "token" in key:
        return str(world.share_tokens[target])
    if "submission" in key:
        return str(world.submission_ids[target])
    if key in {"owner", "org", "account"}:
        return owner
    if key in {"repo", "repo_name", "name"}:
        return repo
    if key in {"full_name", "repo_full_name"}:
        return world.private_repo
    if "issue" in key or key == "number":
        return "9" if target == "completed" else "7"
    if key in {"agent_id", "bot_id"}:
        return world.bot_id("submitter")
    if key == "installation_id":
        return "1"
    if "bounty" in key or key in {"id", "task_id", "item_id"}:
        return str(world.ids[target])
    return None


def _templates(world, method):
    found = {
        route.path
        for route in world.app.routes
        if isinstance(route, APIRoute) and method in route.methods
    }
    for path, operations in (world.app.openapi().get("paths") or {}).items():
        if method.lower() in operations:
            found.add(path)
    return sorted(found)


def _paths(world, method):
    seen = set()
    for target in ("private", "completed"):
        yield from _fill(world, method, target, seen)


def _fill(world, method, target, seen):
    for template in _templates(world, method):
        names = _PARAM.findall(template)
        values = {name: _value(world, name, target) for name in names}
        if any(value is None for value in values.values()):
            continue
        path = template
        for name, value in values.items():
            path = re.sub(r"{" + re.escape(name) + r"(?::[^}]+)?}", value, path)
        if path not in seen:
            seen.add(path)
            yield path


def _sweep(world, names):
    for name in names:
        client = world.client_as(name)
        for path in _paths(world, "GET"):
            client.get(path)
            client.get(path, params={"q": "Private", "include_private": "true", "limit": 100})
            for view in ("completed", "in_progress", "available"):
                if path.endswith("/tasks") or path.endswith("/bounties"):
                    client.get(path, params={"view": view, "status": view})


def test_unauthorized_identities_cannot_read_private_material(sbc_world):
    _sweep(sbc_world, _UNAUTHORIZED)
    private_id = str(sbc_world.ids["private"])
    for name in _UNAUTHORIZED:
        client = sbc_world.client_as(name)
        client.post(f"/api/v1/github/bounties/{private_id}/claim-lease")
        client.post(f"/api/bounties/{private_id}/claim", json={})


def test_revoked_reader_after_cache_expiry(sbc_world):
    reader = sbc_world.client_as("reader")
    reader.get("/api/bounties")
    reader.get(f"/api/bounties/{sbc_world.ids['private']}")
    sbc_world.set_github_access("reader", "none")
    sbc_world.advance_past_access_cache()
    _sweep(sbc_world, ("reader",))
