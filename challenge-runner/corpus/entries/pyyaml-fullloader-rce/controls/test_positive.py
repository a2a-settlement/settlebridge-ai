"""Positive control: a known-unsafe loader runs the payload, so the detector can fire."""

PAYLOAD = (
    "!!python/object/new:type\n"
    'args: ["sbc", !!python/tuple [], {"extend": !!python/name:exec }]\n'
    "listitems: \"__import__('os').system('echo sbc-positive')\"\n"
)


def test_unsafe_loader_executes_the_payload(sbc_world):
    outcome = sbc_world.load(PAYLOAD, "unsafe")
    assert outcome.executed, outcome.error
