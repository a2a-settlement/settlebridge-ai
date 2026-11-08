"""Negative control: a same-host redirect may repeat the credential.

The credential is presented again on the same host. That is not a different
host, so the observer stays silent.
"""


def test_same_host_redirect_is_not_cross_origin(sbc_world):
    credential = sbc_world.issue()
    outcome = sbc_world.request(
        "http://origin.invalid/start",
        headers={"authorization": credential},
        redirect="http://origin.invalid/next",
    )
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert outcome.value["body"] == "ok"
    assert not outcome.leaked
