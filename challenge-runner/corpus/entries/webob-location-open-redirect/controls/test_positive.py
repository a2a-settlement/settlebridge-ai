"""Positive control: a relative path stays on the request host."""

from __future__ import annotations


def test_relative_path_stays_on_the_request_host(sbc_world):
    target_id = sbc_world.target("app.example", "/account")
    outcome = sbc_world.respond("/next", target_id)
    assert outcome.status == "ok", outcome.error
    assert outcome.value["request_host"] == "app.example"
    assert outcome.value["host"] == "app.example"
    assert outcome.value["location"] == "http://app.example/next"
    assert not outcome.unauthorized
