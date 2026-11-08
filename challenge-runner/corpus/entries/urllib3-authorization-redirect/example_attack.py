"""Stand-in challenger: a cross-origin redirect presents an issued credential."""


def test_cross_origin_redirect_presents_credential(sbc_world):
    credential = sbc_world.issue()
    outcome = sbc_world.request(
        "http://origin.invalid/start",
        headers={"authorization": credential},
        redirect="http://other.invalid/land",
    )
    assert outcome.status == "ok", outcome.error
    assert outcome.leaked
