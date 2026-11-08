"""Negative control: an absolute Location keeps the host the caller named.

The value already has a URI scheme, so a different host is the caller's host.
The world records no protected write. The same is true of a relative path
served through the redirect call, which stays on the request host.
"""

from __future__ import annotations


def test_absolute_location_is_the_callers_host(sbc_world):
    target_id = sbc_world.target("app.example", "/account")
    outcome = sbc_world.respond("https://other.example/away", target_id)
    assert outcome.status == "ok", outcome.error
    assert outcome.value["request_host"] == "app.example"
    assert outcome.value["host"] == "other.example"
    assert not outcome.unauthorized
    moved = sbc_world.redirect("/next", target_id)
    assert moved.status == "ok", moved.error
    assert moved.value["host"] == "app.example"
    assert moved.value["location"] == "http://app.example/next"
    assert not moved.unauthorized
