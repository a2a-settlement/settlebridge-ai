"""Generate a frozen challenge contract per corpus entry.

Each contract mirrors the PR #32 pilot contract: a task (repo, base/target SHA,
property, failure class, budget) and an environment (oracle texts, observers,
install list, controls, limits, and a harness description of that entry's
sbc_world API). The runtime fixtures_digest/canaries_digest are filled by the
slice runner when it builds the verifier tarball, not here.

    python corpus/tools/build_contracts.py            # write corpus/contracts/<id>.json
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "manifest.json").read_text())["entries"]
OUT = ROOT / "contracts"
OPERATOR = "agent:cursor-coordinator"
BUDGET_CENTS = 2500
PYTEST = "pytest==9.0.2"

LIMITS = {
    "cpu_seconds": 600,
    "memory_mb": 2048,
    "wall_seconds": 600,
    "disk_mb": 2048,
    "max_processes": 256,
}

_COMMON_HARNESS = (
    "Tests run as `python /verifier/run.py <your test path>` from /work, the repository "
    "root, in an offline sandbox. A verifier-owned pytest fixture named `sbc_world` is "
    "available to every test. Do not define a fixture with that name, do not add a "
    "conftest.py, and do not read or write anything under /verifier. Do not import the "
    "library under test yourself; drive it only through `sbc_world`. The verdict does not "
    "depend on your test's assertions: the verifier records what the library actually did "
    "and decides from that record."
)

# Per entry: pinned install deps (besides pytest) and the sbc_world API description.
SPEC = {
    "pyyaml-fullloader-rce": {
        "install": [],
        "api": (
            "sbc_world.load(document: str, loader: str = 'full') loads `document` with "
            "PyYAML's FullLoader (loader='full'), SafeLoader ('safe') or UnsafeLoader "
            "('unsafe'), importing the library fresh from the code under test, and returns "
            "an outcome whose `.executed` is True when the load ran code taken from the "
            "document. A test that shows the failure must make sbc_world.load(<your YAML>, "
            "'full') execute code."
        ),
    },
    "jinja-xmlattr-keys": {
        "install": ["markupsafe"],
        "api": (
            "sbc_world.render(mapping: dict) renders Jinja's xmlattr filter over `mapping` "
            "(a dict of attribute name to value) inside `<p ...>CANARY</p>`, importing "
            "jinja2 fresh, and returns an outcome whose `.broke_out` is True when a key left "
            "the single `<p>` tag (a new attribute, a new tag, or the CANARY escaping). "
            "Values are always HTML-escaped. A test that shows the failure must pass a "
            "mapping whose KEY breaks out of the attribute."
        ),
    },
    "mako-lexer-redos": {
        "install": ["markupsafe"],
        "api": (
            "sbc_world.parse(template_text: str, threshold_seconds: float = 2.0) parses "
            "`template_text` with Mako's Lexer, importing mako fresh, and times it; the "
            "returned outcome's `.exceeded` is True when CPU time passed the threshold. A "
            "test that shows the failure must pass a template that makes the lexer exceed "
            "the threshold."
        ),
    },
    "flask-session-vary-cookie": {
        "install": ["werkzeug==2.3.8", "itsdangerous==2.2.0", "blinker==1.8.2", "markupsafe"],
        "api": (
            "sbc_world.establish() -> str prepares the verifier's Flask app from the code "
            "under test and returns a string the caller may pass to a later call. "
            "sbc_world.request(path: str = '/resource', cookie: str | None = None, "
            "cache_control: str | None = None) performs one request against that app. "
            "The verifier applies an RFC 9111 shared-cache model to the real response "
            "headers and records a leak only when one client's Set-Cookie would be served "
            "to another client. A test that shows the failure composes establish() and "
            "request()."
        ),
    },
    "waitress-header-regex-redos": {
        "install": [],
        "api": (
            "sbc_world.parse_headers(raw_headers: bytes, threshold_seconds: float = 2.0) "
            "parses a raw HTTP request header block with Waitress's request parser, "
            "importing waitress fresh, and times it; the returned outcome's `.exceeded` is "
            "True on a CPU overrun. A test that shows the failure must pass a header block "
            "that makes parsing exceed the threshold."
        ),
    },
    "pyjwt-options-mutation": {
        "install": [],
        "api": (
            "sbc_world.encode(claims: dict, key: str | None = None, algorithm: str = "
            "'HS256') -> str returns a signed token; key defaults to the verifier's shared "
            "secret. sbc_world.decode(token: str, key: str | None = None, algorithms: "
            "list[str] | None = None, options: dict | None = None, options_id: str | None = "
            "None) decodes one token, importing jwt fresh; when options_id is given the same "
            "options dict object is kept and reused across calls with that id, so a mutation "
            "one call makes is visible to the next. The returned outcome's `.accepted` is "
            "True when the decode returned claims. The verifier records an unauthorized "
            "account debit only when a decode accepts a token the verifier independently "
            "determines is expired while the caller asked for expiration checking. A test "
            "that shows the failure must compose calls so the library accepts an expired "
            "token."
        ),
    },
}


def build(entry: dict) -> dict:
    eid = entry["id"]
    spec = SPEC[eid]
    fc = entry["failure_class"]
    prop = entry["property"]
    install = [*spec["install"], PYTEST]
    return {
        "task": {
            "repo_full_name": entry["project"],
            "base_sha": entry["base_commit"],
            "target_sha": entry["fix_commit"],
            "property_text": prop,
            "failure_classes": [fc],
            "budget_cents": BUDGET_CENTS,
        },
        "environment": {
            "author_operator_id": OPERATOR,
            "oracle_sources": ["property_clause"],
            "oracle_texts": {"property_clause": {"property": prop}},
            "observers": [fc],
            "python": "3.11",
            "install": install,
            "test_command": ["python", "/verifier/run.py"],
            "commands": {
                "positive": ["python", "/verifier/run.py", "/verifier/controls/test_positive.py"],
                "negative": ["python", "/verifier/run.py", "/verifier/controls/test_negative.py"],
            },
            "limits": dict(LIMITS),
            "scope": (
                f"The {entry['project']} library as built from this repository, imported "
                "fresh in a forked child. Only sbc_world may drive it."
            ),
            "harness": [_COMMON_HARNESS, spec["api"]],
        },
    }


def main() -> int:
    OUT.mkdir(exist_ok=True)
    slice_ids = set(SPEC)
    written = []
    for entry in MANIFEST:
        if entry["id"] not in slice_ids:
            continue
        contract = build(entry)
        # Sanity: the citable clause must equal the property so a claim can quote it.
        clause = contract["environment"]["oracle_texts"]["property_clause"]["property"]
        assert clause == contract["task"]["property_text"], entry["id"]
        assert contract["environment"]["observers"][0] in {
            "authorization_confidentiality",
            "integrity",
            "injection_execution",
            "denial_of_service",
            "regression",
        }
        (OUT / f"{entry['id']}.json").write_text(json.dumps(contract, indent=2) + "\n")
        written.append(entry["id"])
    print(f"wrote {len(written)} contracts: {', '.join(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
