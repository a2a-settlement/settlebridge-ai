"""Stand-in challenger: a refreshed session cookie is replayed from a shared cache."""


def test_refreshed_session_cookie_is_cross_served(sbc_world):
    outcome = sbc_world.exchange("refresh")
    assert outcome.status == "ok", outcome.error
    assert outcome.leaked
