"""Platform challenger: claims, prompts, and the challenge_tests bundle."""

from __future__ import annotations

import json
import os
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.schemas.challenge import ClaimIn  # noqa: E402

from challenge_runner.challenger import (  # noqa: E402
    ChallengeBundle,
    package_bundle,
    run_challenger,
)
from challenge_runner.challenger.errors import ChallengerError  # noqa: E402
from challenge_runner.challenger.limits import (  # noqa: E402
    MAX_COMPLETION_TOKENS,
    MAX_MODEL_CHARS,
    MAX_TEST_FILE_BYTES,
)
from challenge_runner.sandbox.tarutil import safe_extract  # noqa: E402

DIFF = """\
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""

_SENTINEL = "MODEL_TEXT_SHOULD_NOT_BE_STORED"


class _Omit:
    pass


_OMIT = _Omit()


class FakeClient:
    def __init__(self, payload: str, usage: dict | None | _Omit = _OMIT):
        self.payload = payload
        if isinstance(usage, _Omit):
            self.usage: dict | None = {"input_tokens": 11, "output_tokens": 13}
        else:
            self.usage = usage
        self.calls: list[dict] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
        if self.usage is not None:
            self.last_usage = dict(self.usage)
        return self.payload


def _contract(**extra) -> dict:
    base = {
        "property": "The add function returns the sum of its inputs.",
        "failure_classes": ["integrity", "regression"],
        "oracle_sources": ["property_clause", "existing_test"],
        "environment": {"python": "3.11", "install": "pip install -e ."},
        "budget_cents": 2500,
    }
    base.update(extra)
    return base


def _claim(
    path: str = "challenge_tests/test_gap.py",
    content: str | None = None,
    **overrides,
) -> dict:
    body = {
        "failure_class": "integrity",
        "scenario": {
            "precondition": "The module is imported.",
            "setup": "Import pkg.mod.",
            "action": "Call add(1, 1).",
            "test_path": path,
        },
        "oracle_citation": {
            "source": "property_clause",
            "reference": "property",
            "quote": "The add function returns the sum of its inputs.",
        },
        "expected": "add(1, 1) returns 2.",
        "observed": "add(1, 1) returns 0.",
        "claimed_impact": "Callers that trust the sum take the wrong branch.",
        "test_files": [
            {
                "path": path,
                "content": content or "def test_add_sum():\n    assert False\n",
            }
        ],
    }
    body.update(overrides)
    return body


def _source(root: Path) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "mod.py").write_text("TOUCHED_BODY_cc33\ndef add(a, b):\n    return a + b\n")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_mod.py").write_text(
        "NEAREST_TEST_dd44\ndef test_add():\n    assert True\n"
    )
    (root / "other").mkdir(exist_ok=True)
    (root / "other" / "unrelated.py").write_text("UNRELATED_BODY_ee55\n")


def _bundle(tmp_path: Path, **contract_extra) -> tuple[ChallengeBundle, Path]:
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    _source(source)
    bundle = ChallengeBundle(
        contract=_contract(**contract_extra),
        source_dir=source,
        diff_text=DIFF,
    )
    return bundle, tmp_path / "out"


def _run(
    tmp_path: Path,
    payload: str,
    *,
    max_claims: int = 5,
    usage: dict | None | _Omit = _OMIT,
    **extra,
):
    bundle, out = _bundle(tmp_path, **extra)
    client = FakeClient(payload, usage=usage)
    claims = run_challenger(bundle, out_dir=out, model_client=client, max_claims=max_claims)
    return claims, out, client


def _text_files(out: Path) -> str:
    chunks: list[str] = []
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".py", ".txt"}:
            chunks.append(path.read_text())
    return "\n".join(chunks)


def _rejected(out: Path) -> dict:
    return json.loads((out / "rejected.json").read_text())


def test_happy_path_writes_claim_tests_usage_and_bundle(tmp_path: Path):
    content = "def test_add_sum():\n    assert False\n"
    claims, out, client = _run(tmp_path, json.dumps([_claim(content=content)]))
    assert len(claims) == 1
    ClaimIn.model_validate(asdict(claims[0]))
    assert json.loads((out / "claims.json").read_text()) == [asdict(claims[0])]
    written = out / "challenge_tests" / "test_gap.py"
    assert written.read_text() == content
    assert json.loads((out / "usage.json").read_text()) == {"calls": 1, "model_tokens": 24}
    assert _rejected(out) == {"count": 0, "reasons": {}}
    assert client.calls[0]["max_tokens"] == MAX_COMPLETION_TOKENS
    prompt = client.calls[0]["system"] + client.calls[0]["prompt"]
    assert "The add function returns the sum of its inputs." in prompt
    assert "2500" in prompt
    assert "3.11" in prompt
    assert "return a - b" in prompt
    assert "untrusted" in client.calls[0]["system"].lower()

    dest = tmp_path / "extracted"
    safe_extract(out / "challenger_bundle.tar.gz", dest, required_prefix="challenge_tests")
    assert (dest / "challenge_tests" / "test_gap.py").read_text() == content


def test_property_text_alias_is_used_when_property_is_absent(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "pkg.py").write_text("x = 1\n")
    bundle = ChallengeBundle(
        contract={
            "property_text": "Inputs are not echoed to another tenant.",
            "failure_classes": ["authorization_confidentiality"],
            "oracle_sources": ["customer_rule"],
            "environment": {"python": "3.11"},
            "budget_cents": 40,
        },
        source_dir=source,
        diff_text="diff --git a/pkg.py b/pkg.py\n--- a/pkg.py\n+++ b/pkg.py\n",
    )
    client = FakeClient("[]")
    run_challenger(bundle, out_dir=tmp_path / "out", model_client=client)
    assert "Inputs are not echoed to another tenant." in client.calls[0]["prompt"]


def test_fenced_json_is_accepted(tmp_path: Path):
    payload = "Working from the diff:\n```json\n" + json.dumps([_claim()]) + "\n```\n"
    claims, out, _client = _run(tmp_path, payload)
    assert len(claims) == 1
    ClaimIn.model_validate(asdict(claims[0]))
    assert (out / "challenge_tests" / "test_gap.py").is_file()
    assert _rejected(out)["count"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        "this is not json " + _SENTINEL,
        "```json\n{not json " + _SENTINEL + "}\n```",
        "```\n[1, 2,]\n```",
    ],
)
def test_malformed_json_is_rejected_without_storing_model_text(tmp_path: Path, payload: str):
    claims, out, _client = _run(tmp_path, payload)
    assert claims == []
    assert _rejected(out) == {"count": 1, "reasons": {"not_json": 1}}
    assert _SENTINEL not in _text_files(out)
    assert not list((out / "challenge_tests").rglob("*.py"))


def test_non_list_and_non_object_items_are_counted(tmp_path: Path):
    claims, out, _client = _run(tmp_path, json.dumps({"claims": [_claim()]}))
    assert claims == []
    assert _rejected(out)["reasons"] == {"not_a_list": 1}

    claims, out, _client = _run(tmp_path, json.dumps([1, None, "x"]))
    assert claims == []
    assert _rejected(out) == {"count": 3, "reasons": {"not_an_object": 3}}


def test_oversized_output_is_rejected_without_parsing(tmp_path: Path):
    payload = _SENTINEL * ((MAX_MODEL_CHARS // len(_SENTINEL)) + 2)
    assert len(payload) > MAX_MODEL_CHARS
    claims, out, _client = _run(tmp_path, payload)
    assert claims == []
    rejected = (out / "rejected.json").read_text()
    assert _rejected(out)["reasons"] == {"oversized_output": 1}
    assert _SENTINEL not in rejected
    assert len(rejected) < 500


def test_oversized_fields_and_test_files_are_dropped(tmp_path: Path):
    long_claim = _claim(path="challenge_tests/test_long.py")
    long_claim["expected"] = "Z" * 4001
    huge = _claim(path="challenge_tests/test_huge.py", content="x" * (MAX_TEST_FILE_BYTES + 1))
    good = _claim(path="challenge_tests/test_ok.py", content="def test_ok():\n    pass\n")
    claims, out, _client = _run(tmp_path, json.dumps([long_claim, huge, good]))
    assert [claim.scenario["test_path"] for claim in claims] == ["challenge_tests/test_ok.py"]
    reasons = _rejected(out)["reasons"]
    assert reasons["field_too_long"] == 1
    assert reasons["test_file_too_large"] == 1
    stored = _text_files(out)
    assert "ZZZZ" not in stored
    assert "x" * 80 not in stored
    assert not (out / "challenge_tests" / "test_huge.py").exists()


def test_path_traversal_and_absolute_paths_are_rejected(tmp_path: Path):
    bad_paths = [
        "challenge_tests/../../outside.py",
        "/tmp/test_abs.py",
        "challenge_tests/sub/../../../evil.py",
        "challenge_tests\\test_win.py",
    ]
    claims = []
    for path in bad_paths:
        claim = _claim(path=path)
        claim["expected"] = _SENTINEL
        claims.append(claim)
    claims.append(_claim(path="challenge_tests/test_ok.py", content="def test_ok():\n    pass\n"))
    result, out, _client = _run(tmp_path, json.dumps(claims))
    assert [claim.scenario["test_path"] for claim in result] == ["challenge_tests/test_ok.py"]
    assert _rejected(out)["reasons"]["unsafe_test_path"] == len(bad_paths)
    assert _SENTINEL not in (out / "rejected.json").read_text()
    written = list((out / "challenge_tests").rglob("*.py"))
    assert written == [out / "challenge_tests" / "test_ok.py"]
    assert list(tmp_path.rglob("outside.py")) == []
    assert list(tmp_path.rglob("evil.py")) == []
    assert list(tmp_path.rglob("test_abs.py")) == []
    assert list(tmp_path.rglob("test_win.py")) == []


def test_invalid_enums_are_dropped(tmp_path: Path):
    bad_class = _claim(path="challenge_tests/test_bad_class.py")
    bad_class["failure_class"] = "not_a_class"
    bad_source = _claim(path="challenge_tests/test_bad_source.py")
    bad_source["oracle_citation"] = {
        "source": "rumor",
        "reference": "property",
        "quote": "The add function returns the sum of its inputs.",
    }
    good = _claim(path="challenge_tests/test_ok.py")
    result, out, _client = _run(tmp_path, json.dumps([bad_class, bad_source, good]))
    assert len(result) == 1
    assert result[0].failure_class == "integrity"
    assert _rejected(out)["reasons"]["invalid_failure_class"] == 1
    assert _rejected(out)["reasons"]["invalid_oracle_source"] == 1


def test_max_claims_keeps_the_first_valid_claims(tmp_path: Path):
    payload = json.dumps(
        [
            _claim(
                path=f"challenge_tests/test_{index}.py",
                content=f"def test_{index}():\n    pass\n",
            )
            for index in range(3)
        ]
    )
    result, out, _client = _run(tmp_path, payload, max_claims=1)
    assert len(result) == 1
    assert result[0].scenario["test_path"] == "challenge_tests/test_0.py"
    assert (out / "challenge_tests" / "test_0.py").is_file()
    assert not (out / "challenge_tests" / "test_1.py").exists()
    assert not (out / "challenge_tests" / "test_2.py").exists()
    assert _rejected(out) == {"count": 2, "reasons": {"over_max_claims": 2}}


def test_max_claims_rejects_booleans_and_negatives(tmp_path: Path):
    bundle, out = _bundle(tmp_path)
    client = FakeClient("[]")
    with pytest.raises(ValueError):
        run_challenger(
            bundle,
            out_dir=out,
            model_client=client,
            max_claims=True,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        run_challenger(bundle, out_dir=out, model_client=client, max_claims=-1)
    assert client.calls == []


def test_prompt_excludes_neighbor_fixtures_observers_and_canaries(tmp_path: Path):
    sentinels = {
        "fixture": "FIXTURE_FILE_9f3a2c",
        "observer": "OBSERVER_FILE_9f3a2c",
        "canary": "CANARY_FILE_9f3a2c",
    }
    for name, text in sentinels.items():
        directory = tmp_path / f"{name}s"
        directory.mkdir()
        (directory / "secret.txt").write_text(text + "\n")
    source = tmp_path / "src"
    source.mkdir()
    _source(source)
    (source / "escape.py").symlink_to(tmp_path / "fixtures" / "secret.txt")
    (source / "linked_fixtures").symlink_to(tmp_path / "fixtures", target_is_directory=True)
    diff = DIFF + (
        "diff --git a/../fixtures/secret.txt b/../fixtures/secret.txt\n"
        "--- a/../fixtures/secret.txt\n"
        "+++ b/../fixtures/secret.txt\n"
    )
    contract = _contract(
        observers=["CONTRACT_OBSERVER_9f3a2c"],
        fixtures_digest="CONTRACT_FIXTURE_DIGEST_9f3a2c",
        canaries=["CONTRACT_CANARY_9f3a2c"],
        narrative="CONTRACT_NARRATIVE_9f3a2c",
        failure_classes=["integrity", "NOT_A_CLASS_9f3a2c"],
    )
    client = FakeClient("[]", usage=None)
    bundle = ChallengeBundle(contract=contract, source_dir=source, diff_text=diff)
    run_challenger(bundle, out_dir=tmp_path / "out", model_client=client)
    blob = client.calls[0]["system"] + "\n" + client.calls[0]["prompt"]
    for text in sentinels.values():
        assert text not in blob
    for text in (
        "CONTRACT_OBSERVER_9f3a2c",
        "CONTRACT_FIXTURE_DIGEST_9f3a2c",
        "CONTRACT_CANARY_9f3a2c",
        "CONTRACT_NARRATIVE_9f3a2c",
        "NOT_A_CLASS_9f3a2c",
    ):
        assert text not in blob
    assert str(tmp_path / "fixtures") not in blob
    assert "TOUCHED_BODY_cc33" in blob
    assert "NEAREST_TEST_dd44" in blob
    assert "UNRELATED_BODY_ee55" not in blob
    assert "other/unrelated.py" in blob
    assert "U|" in blob
    assert json.loads((tmp_path / "out" / "usage.json").read_text()) == {
        "calls": 1,
        "model_tokens": 0,
    }


def test_source_excerpts_are_size_capped(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "big.py").write_text(
        "HEAD_MARKER\n" + ("a" * 100_000) + "\nTAIL_MARKER_NOT_INCLUDED\n"
    )
    diff = "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -1 +1 @@\n-a\n+b\n"
    client = FakeClient("[]")
    bundle = ChallengeBundle(contract=_contract(), source_dir=source, diff_text=diff)
    run_challenger(bundle, out_dir=tmp_path / "out", model_client=client)
    prompt = client.calls[0]["prompt"]
    assert "HEAD_MARKER" in prompt
    assert "TAIL_MARKER_NOT_INCLUDED" not in prompt
    assert len(prompt) < 80_000


def test_model_error_records_usage_without_the_exception_text(tmp_path: Path):
    bundle, out = _bundle(tmp_path)

    class Boom(Exception):
        pass

    class Failing:
        def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
            self.last_usage = {"input_tokens": 2, "output_tokens": 3}
            raise Boom("SECRET_MODEL_ERROR_TEXT")

    with pytest.raises(Boom):
        run_challenger(bundle, out_dir=out, model_client=Failing())
    assert json.loads((out / "usage.json").read_text()) == {"calls": 1, "model_tokens": 5}
    assert "SECRET_MODEL_ERROR_TEXT" not in _text_files(out)


def test_nested_symlink_under_challenge_tests_is_not_followed(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    bundle, out = _bundle(tmp_path)
    tests = out / "challenge_tests"
    tests.mkdir(parents=True)
    (tests / "sub").symlink_to(outside, target_is_directory=True)
    payload = json.dumps(
        [
            _claim(
                path="challenge_tests/sub/test_escape.py",
                content="def test_escape():\n    pass\n",
            )
        ]
    )
    result, out, _client = _run(tmp_path, payload)
    assert result == []
    assert _rejected(out)["reasons"]["unsafe_test_path"] == 1
    assert list(outside.iterdir()) == []


def test_symlinked_challenge_tests_is_not_followed(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (out / "challenge_tests").symlink_to(outside, target_is_directory=True)
    bundle = ChallengeBundle(contract=_contract(), source_dir=tmp_path / "missing", diff_text="")
    client = FakeClient(json.dumps([_claim()]))
    with pytest.raises(ChallengerError):
        run_challenger(bundle, out_dir=out, model_client=client)
    assert client.calls == []
    assert list(outside.iterdir()) == []


def test_package_bundle_is_deterministic_and_extracts(tmp_path: Path):
    def fill(root: Path, mtime: int) -> None:
        nested = root / "challenge_tests" / "nested"
        nested.mkdir(parents=True)
        first = root / "challenge_tests" / "test_b.py"
        second = nested / "test_a.py"
        first.write_text("def test_b():\n    pass\n")
        second.write_text("def test_a():\n    pass\n")
        first.chmod(0o755)
        second.chmod(0o707)
        os.utime(first, (mtime, mtime))
        os.utime(second, (mtime, mtime))
        (root / "challenge_tests" / "link.py").symlink_to(first)
        (root / "notes.txt").write_text("do not pack\n")

    left = tmp_path / "left"
    right = tmp_path / "right"
    fill(left, 10)
    fill(right, 222)
    first = package_bundle(left)
    again = package_bundle(left)
    other = package_bundle(right)
    assert first.read_bytes() == again.read_bytes() == other.read_bytes()

    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
    names = [member.name for member in members]
    assert names == [
        "challenge_tests/nested/test_a.py",
        "challenge_tests/test_b.py",
    ]
    for member in members:
        assert member.isfile()
        assert member.mtime == 0
        assert member.uid == 0 and member.gid == 0
        assert stat_mode(member.mode) == 0o644
        assert member.name.startswith("challenge_tests/")

    dest = tmp_path / "extracted"
    safe_extract(first, dest, required_prefix="challenge_tests")
    assert (dest / "challenge_tests" / "test_b.py").read_text() == "def test_b():\n    pass\n"
    assert (dest / "challenge_tests" / "nested" / "test_a.py").read_text().startswith("def test_a")
    assert not (dest / "notes.txt").exists()
    assert not (dest / "challenge_tests" / "link.py").exists()


def stat_mode(mode: int) -> int:
    return mode & 0o777
