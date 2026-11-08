"""Negative control: a long run of ordinary letters stays under the CPU budget.

Length alone must not trip the observer. The library may reject the input
as too long; it must do so without a CPU overrun on encode or decode.
"""

DOMAIN = "a" * 60000


def test_long_ordinary_label_stays_under_budget(sbc_world):
    encoded = sbc_world.encode(DOMAIN)
    assert not encoded.killed
    assert not encoded.exceeded
    decoded = sbc_world.decode(DOMAIN)
    assert not decoded.killed
    assert not decoded.exceeded
