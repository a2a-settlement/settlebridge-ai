"""Stand-in challenger: a redirect delivers the proxy credential to the destination."""


def test_redirect_delivers_proxy_credential(sbc_world):
    secret = sbc_world.credential()
    outcome = sbc_world.request("resource", secret=secret, redirect="https")
    assert outcome.status == "ok", outcome.error
    assert outcome.leaked
