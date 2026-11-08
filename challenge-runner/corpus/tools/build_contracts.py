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
    "babel-locale-path-traversal": {
        "install": [],
        "api": (
            "sbc_world.exists(name: str) checks whether a locale name is present, importing "
            "babel fresh. sbc_world.load(name: str, merge_inherited: bool = True) loads that "
            "locale. The verifier records an injection only when the name reaches a path "
            "position the verifier parses. A test that shows the failure must pass a name "
            "the library treats as a path."
        ),
    },
    "requests-proxy-authorization": {
        "install": [
            "urllib3==2.6.3",
            "certifi==2026.02.25",
            "idna==3.20",
            "charset-normalizer==3.4.5",
        ],
        "api": (
            "sbc_world.credential() returns a credential string the caller may pass to a "
            "later call. sbc_world.request(target: str = 'plain', secret: str | None = None, "
            "redirect: str | None = None) performs one request through the requests library "
            "under test, importing it fresh. The verifier records an authorization failure "
            "when a credential is disclosed to a target the caller did not choose. A test "
            "that shows the failure composes credential() and request()."
        ),
    },
    "urllib3-authorization-redirect": {
        "install": [],
        "api": (
            "sbc_world.issue() prepares a value the caller may pass to a later call. "
            "sbc_world.request(url: str, headers: dict | None = None, redirect: str | None = "
            "None) performs one request through urllib3, importing the library fresh. The "
            "verifier records an authorization failure when a credential is disclosed to a "
            "target the caller did not choose. A test that shows the failure composes "
            "issue() and request()."
        ),
    },
    "idna-long-input-dos": {
        "install": [],
        "api": (
            "sbc_world.encode(domain: str, threshold_seconds: float = 2.0) encodes one "
            "domain with idna, importing the library fresh, and times it. "
            "sbc_world.decode(domain: str, threshold_seconds: float = 2.0) decodes one "
            "domain the same way. The verifier records a denial of service when CPU time "
            "passes threshold_seconds. A test that shows the failure must pass a domain "
            "that makes encode or decode exceed the threshold."
        ),
    },
    "python-multipart-content-type-redos": {
        "install": [],
        "api": (
            "sbc_world.parse_content_type(content_type: str, threshold_seconds: float = 2.0) "
            "parses one Content-Type value with python-multipart, importing the library "
            "fresh, and times it. The verifier records a denial of service when CPU time "
            "passes threshold_seconds. A test that shows the failure must pass a content "
            "type that makes parsing exceed the threshold."
        ),
    },
    "mistune-emphasis-redos": {
        "install": [],
        "api": (
            "sbc_world.render(markdown_text: str, threshold_seconds: float) renders "
            "`markdown_text` with Mistune, importing mistune fresh, and times it. The "
            "verifier records a denial of service when CPU time passes threshold_seconds. "
            "A test that shows the failure must pass markdown that makes rendering exceed "
            "the threshold."
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
    "webob-location-open-redirect": {
        "install": [],
        "api": (
            "sbc_world.target(host: str, path: str = '/', scheme: str = 'http') prepares "
            "one request target from the code under test and returns an id. "
            "sbc_world.respond(location: str, target_id) records one Location response for "
            "that target. sbc_world.redirect(location: str, target_id) interprets one "
            "redirect Location against that target, importing webob fresh. The verifier "
            "records an integrity failure when a redirect is accepted that leaves the "
            "target the caller established. A test that shows the failure composes "
            "target() and redirect()."
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
    "python-jose-hmac-key-confusion": {
        "install": [],
        "api": (
            "sbc_world.public_key(encoding: str = 'DER') returns a public key from the code "
            "under test. sbc_world.encode(claims: dict, key: str | None = None, algorithm: "
            "str = 'HS256') returns a signed token. sbc_world.decode(token: str, key: str | "
            "None = None, algorithms: list[str] | None = None) decodes one token, importing "
            "the library fresh. The verifier records an integrity failure when an HS256 "
            "token signed with an asymmetric public key is accepted. A test that shows the "
            "failure composes public_key(), encode(), and decode()."
        ),
    },
    "starlette-multipart-field-dos": {
        "install": ["anyio==4.12.1", "python-multipart==0.0.33"],
        "api": (
            "sbc_world.field(name: str, value: str) and sbc_world.file(name: str, filename: "
            "str, value: str) build multipart parts. sbc_world.parse(parts, threshold_mb: "
            "float = 1.0) parses those parts with Starlette, importing the library fresh. "
            "The verifier records a denial of service when parsing exceeds threshold_mb. A "
            "test that shows the failure composes field() or file() with parse()."
        ),
    },
    "authlib-oct-key-confusion": {
        "install": [],
        "api": (
            "sbc_world.public_key() returns a public key from the code under test. "
            "sbc_world.encode(claims: dict, key: str | None = None, algorithm: str = 'HS256') "
            "returns a signed token. sbc_world.decode(token: str, key: str | None = None, "
            "algorithms: list[str] | None = None) decodes one token, importing authlib fresh. "
            "The verifier records an integrity failure when an HMAC-signed token is accepted "
            "with an asymmetric public key or certificate as the secret. A test that shows "
            "the failure composes public_key(), encode(), and decode()."
        ),
    },
    "pyjwt-pyjwk-algorithm-allowlist": {
        "install": [],
        "api": (
            "sbc_world.encode(claims: dict, key: str | None = None, algorithm: str = 'HS256', "
            "header_algorithm: str | None = None) returns a signed token. sbc_world.jwk(key: "
            "str | None = None, algorithm: str = 'HS256') returns a PyJWK. "
            "sbc_world.decode(token: str, key: str | None = None, algorithms: list[str] | "
            "None = None, jwk=None) decodes one token, importing jwt fresh. The verifier "
            "records an integrity failure when decode with a PyJWK key accepts an algorithm "
            "outside the caller's algorithms allow-list. A test that shows the failure "
            "composes encode(), jwk(), and decode()."
        ),
    },
    "tornado-static-open-redirect": {
        "install": [],
        "api": (
            "sbc_world.prepare() prepares the verifier's static-file handler from the code "
            "under test. sbc_world.fetch(path: str) asks that handler for one path, importing "
            "tornado fresh. sbc_world.close() releases it. The verifier records an integrity "
            "failure when the handler redirects to a different host. A test that shows the "
            "failure composes prepare() and fetch()."
        ),
    },
    "tornado-cookie-attribute-injection": {
        "install": [],
        "api": (
            "sbc_world.set_cookie(name: str, value: str, attributes=None) sets one cookie "
            "through Tornado's RequestHandler, importing tornado fresh. The verifier records "
            "an injection only when a caller-supplied value adds or changes a cookie "
            "attribute. A test that shows the failure calls set_cookie()."
        ),
    },
    "gunicorn-request-smuggling": {
        "install": [],
        "api": (
            "sbc_world.feed(data) supplies bytes to Gunicorn's request parser, importing "
            "gunicorn fresh. sbc_world.read_request() returns the next parsed request id. "
            "sbc_world.transfer(request_id) moves that request on. The verifier records an "
            "integrity failure when Transfer-Encoding or Content-Length would let a proxy "
            "and Gunicorn disagree on where a request ends. A test that shows the failure "
            "composes feed() and read_request()."
        ),
    },
    "pyjwt-hmac-key-confusion": {
        "install": ["cryptography==50.0.2"],
        "api": (
            "sbc_world.encode(claims: dict, key: str | None = None, algorithm: str = 'HS256') "
            "returns a signed token. sbc_world.decode(token: str, key: str | None = None, "
            "algorithms: list[str] | None = None) decodes one token, importing jwt fresh. The "
            "verifier records an integrity failure when an HS256 token signed with an "
            "asymmetric public key is accepted. A test that shows the failure composes "
            "encode() and decode()."
        ),
    },
    "waitress-request-smuggling": {
        "install": [],
        "api": (
            "sbc_world.receive(raw) supplies one raw request to Waitress, importing waitress "
            "fresh from src. sbc_world.apply(identity) applies one parsed request identity. "
            "The verifier records an integrity failure when a chunk size or Content-Length "
            "would be read differently by a proxy. A test that shows the failure composes "
            "receive() and apply()."
        ),
    },
    "aiohttp-static-traversal": {
        "install": [
            "aiohappyeyeballs==2.6.1",
            "aiosignal==1.4.0",
            "frozenlist==1.8.0",
            "multidict==6.7.1",
            "propcache==0.4.1",
            "yarl==1.22.0",
        ],
        "api": (
            "sbc_world.prepare(follow_symlinks: bool = False) prepares the verifier's "
            "static route from the code under test. sbc_world.fetch(site: str, name: str) "
            "asks that route for one name, importing aiohttp fresh. sbc_world.close() "
            "releases it. The verifier records an authorization failure when a file outside "
            "the configured directory is served. A test that shows the failure composes "
            "prepare() and fetch()."
        ),
    },
    "datamodel-codegen-validator-injection": {
        "install": [],
        "api": (
            "sbc_world.load_schema(schema) loads one schema, importing "
            "datamodel-code-generator fresh from src. sbc_world.generate(template_data) "
            "generates code from that schema and the supplied template data. The verifier "
            "records an injection only when generated code contains a Python expression "
            "taken from template_data. A test that shows the failure composes load_schema() "
            "and generate()."
        ),
    },
    "setuptools-package-index-redos": {
        "install": [],
        "api": (
            "sbc_world.parse_page(page: str, threshold_seconds: float = 2.0) parses one HTML "
            "package-index page with setuptools, importing the library fresh, and times it. "
            "The verifier records a denial of service when CPU time passes threshold_seconds. "
            "A test that shows the failure must pass a page that makes parsing exceed the "
            "threshold."
        ),
    },
    "sb-pr32": {
        "install": [],
        "api": (
            "sbc_world.read_bounty(actor, bounty) reads one bounty as that actor. "
            "sbc_world.read_submission(actor, bounty) reads one submission for that bounty. "
            "sbc_world.list_bounties(actor) lists bounties visible to that actor. The code "
            "under test is imported fresh from backend. The verifier records an authorization "
            "failure when a bounty on a private repository is visible or claimable by a "
            "signed-in user whose linked GitHub account lacks read access. A test that shows "
            "the failure composes these calls."
        ),
    },
    "twisted-request-smuggling": {
        "install": [
            "incremental==24.11.0",
            "zope.interface==8.7",
            "constantly==23.10.4",
            "Automat==25.4.16",
            "hyperlink==21.0.0",
            "attrs==25.4.0",
            "typing_extensions==4.15.0",
        ],
        "api": (
            "sbc_world.feed(data) supplies bytes to Twisted's request parser, importing "
            "twisted fresh from src. sbc_world.parse() parses one request from those bytes. "
            "sbc_world.balance() asks the parser for the balance of that request. The "
            "verifier records an integrity failure when a header or chunk size would be read "
            "differently by a proxy. A test that shows the failure composes feed() and parse()."
        ),
    },
    "django-truncator-html-redos": {
        "install": ["asgiref==3.13.0", "sqlparse==0.6.1"],
        "api": (
            "sbc_world.chars(text: str, num, threshold_seconds: float = 2.0) and "
            "sbc_world.words(text: str, num, threshold_seconds: float = 2.0) truncate text "
            "with Django's Truncator, importing django fresh, and time the call. The "
            "verifier records a denial of service when CPU time passes threshold_seconds. "
            "A test that shows the failure must pass text that makes chars() or words() "
            "exceed the threshold."
        ),
    },
    "django-jsonfield-values-sqli": {
        "install": ["asgiref==3.13.0", "sqlparse==0.6.1"],
        "api": (
            "sbc_world.values_sql(key) and sbc_world.values_list_sql(key) compile a values "
            "or values_list selection for one JSONField key, importing django fresh. "
            "sbc_world.filter_sql(key) compiles a filter for that key. The verifier records "
            "an injection only when a caller-supplied key reaches the SQL text unquoted. A "
            "test that shows the failure composes values_sql() or values_list_sql()."
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
