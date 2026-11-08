"""Negative control: a plaintext redirect does not deliver the proxy credential.

The measured request still follows a redirect through an authenticated proxy.
That hop is not a tunnel, so the destination does not receive the credential.
The observer stays silent.
"""


def test_plaintext_redirect_is_not_delivered(sbc_world):
    secret = sbc_world.credential()
    outcome = sbc_world.request("resource", secret=secret, redirect="http")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert outcome.value["body"] == "resource"
    assert not outcome.leaked
