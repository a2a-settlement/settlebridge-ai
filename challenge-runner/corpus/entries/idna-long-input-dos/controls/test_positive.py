"""Positive control: a normal domain encodes and decodes."""

DOMAIN = "example.com"


def test_normal_domain_round_trips(sbc_world):
    encoded = sbc_world.encode(DOMAIN)
    assert encoded.status == "ok", encoded.error
    assert encoded.value == DOMAIN
    assert not encoded.exceeded
    decoded = sbc_world.decode(DOMAIN)
    assert decoded.status == "ok", decoded.error
    assert decoded.value == DOMAIN
    assert not decoded.exceeded
