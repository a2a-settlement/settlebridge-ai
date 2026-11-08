"""Stand-in challenger: a relative Location is served on another host."""

from __future__ import annotations


def test_relative_location_leaves_the_request_host(sbc_world):
    target_id = sbc_world.target("app.example", "/account")
    outcome = sbc_world.respond(" //other.example/x", target_id)
    assert outcome.status == "ok", outcome.error
    assert outcome.unauthorized
    assert outcome.value["host"] != outcome.value["request_host"]
