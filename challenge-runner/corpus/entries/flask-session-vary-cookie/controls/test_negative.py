"""Negative control: a private response is not served to another client.

The measured view does not touch the session, so a vulnerable Flask still
refreshes ``Set-Cookie`` without ``Vary: Cookie``. ``Cache-Control: private``
keeps the shared-cache model from delivering that cookie. The observer stays
silent.
"""


def test_private_response_is_not_cross_served(sbc_world):
    cookie = sbc_world.establish()
    outcome = sbc_world.request(cookie=cookie, cache_control="private")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert outcome.value["body"] == "resource"
    assert not outcome.leaked
