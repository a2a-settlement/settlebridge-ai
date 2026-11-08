"""Stand-in: a PyJWK algorithm outside the caller's allow-list is accepted."""

from __future__ import annotations


def test_pyjwk_algorithm_outside_allowlist_is_accepted(sbc_world):
    token = sbc_world.encode(
        {"sub": "attacker"},
        algorithm="HS256",
        header_algorithm="HS384",
    )
    outcome = sbc_world.decode(
        token,
        algorithms=["HS384"],
        jwk=sbc_world.jwk(algorithm="HS256"),
    )
    assert outcome.accepted, outcome.error
    assert outcome.unauthorized
    assert outcome.value["sub"] == "attacker"
