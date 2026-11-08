"""Negative control: the safe loader rejects the payload, so the detector stays silent."""

PAYLOAD = (
    "!!python/object/new:type\n"
    'args: ["sbc", !!python/tuple [], {"extend": !!python/name:exec }]\n'
    "listitems: \"__import__('os').system('echo sbc-negative')\"\n"
)


def test_safe_loader_does_not_execute(sbc_world):
    outcome = sbc_world.load(PAYLOAD, "safe")
    assert not outcome.executed
