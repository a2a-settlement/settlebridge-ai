"""Positive control: declared attributes are emitted as those attributes."""


def test_declared_attributes_round_trip(sbc_world):
    outcome = sbc_world.set_cookie(
        "sid",
        "ok",
        {
            "domain": "example.com",
            "path": "/app",
            "httponly": True,
            "samesite": "Lax",
            "secure": True,
        },
    )
    assert outcome.status == "ok", outcome.error
    assert outcome.injected is False
    assert outcome.value["header"] == (
        "sid=ok; Domain=example.com; HttpOnly; Path=/app; SameSite=Lax; Secure"
    )
