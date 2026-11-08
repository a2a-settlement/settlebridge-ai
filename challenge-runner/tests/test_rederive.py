"""Independent re-deriver: sees the scenario, never the challenger's narrative or tests."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from challenge_runner.challenger import ChallengeBundle
from challenge_runner.challenger.rederive import run_rederiver

DIFF = """\
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""

CLAIM = {
    "failure_class": "integrity",
    "scenario": {
        "precondition": "PRECONDITION_TEXT",
        "setup": "SETUP_TEXT",
        "action": "ACTION_TEXT",
        "test_path": "challenge_tests/test_gap.py",
    },
    "oracle_citation": {"source": "property_clause", "reference": "property", "quote": "sum"},
    "expected": "EXPECTED_NARRATIVE",
    "observed": "OBSERVED_NARRATIVE",
    "claimed_impact": "IMPACT_NARRATIVE",
    "test_files": [{"path": "challenge_tests/test_gap.py", "content": "CHALLENGER_TEST_BODY"}],
}


class FakeClient:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls: list[dict] = []
        self.last_usage = {"input_tokens": 5, "output_tokens": 7}

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        self.calls.append({"system": system, "prompt": prompt})
        return self.payload


def _bundle(tmp_path: Path) -> ChallengeBundle:
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True, exist_ok=True)
    (src / "pkg" / "mod.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    contract = {
        "property": "The add function returns the sum of its inputs.",
        "failure_classes": ["integrity"],
        "oracle_sources": ["property_clause"],
    }
    return ChallengeBundle(contract=contract, source_dir=src, diff_text=DIFF)


def _answer(path: str = "challenge_tests/test_rederived.py", **extra) -> str:
    body = {
        "test_path": path,
        "test_files": [{"path": path, "content": "def test_r():\n    pass\n"}],
        **extra,
    }
    return json.dumps(body)


def test_prompt_carries_the_scenario_but_not_the_narrative_or_tests(tmp_path):
    client = FakeClient(_answer())
    run_rederiver(_bundle(tmp_path), CLAIM, out_dir=tmp_path / "out", model_client=client)
    sent = client.calls[0]["system"] + client.calls[0]["prompt"]
    for text in ("PRECONDITION_TEXT", "SETUP_TEXT", "ACTION_TEXT"):
        assert "S| " in sent and text in sent
    for text in (
        "EXPECTED_NARRATIVE",
        "OBSERVED_NARRATIVE",
        "IMPACT_NARRATIVE",
        "CHALLENGER_TEST_BODY",
    ):
        assert text not in sent
    assert "challenge_tests/test_gap.py" not in sent


def test_bundle_holds_only_the_rederived_test(tmp_path):
    client = FakeClient(_answer())
    result = run_rederiver(_bundle(tmp_path), CLAIM, out_dir=tmp_path / "out", model_client=client)
    assert result is not None
    assert result.test_path == "challenge_tests/test_rederived.py"
    with tarfile.open(result.bundle_path) as tar:
        names = [member.name for member in tar.getmembers() if member.isfile()]
    assert names == ["challenge_tests/test_rederived.py"]
    usage = json.loads((tmp_path / "out" / "usage.json").read_text(encoding="utf-8"))
    assert usage == {"calls": 1, "model_tokens": 12}


def test_unsafe_or_malformed_output_yields_no_rederivation(tmp_path):
    for index, payload in enumerate(
        (
            "not json",
            json.dumps([{"test_path": "challenge_tests/t.py"}]),
            _answer("conftest.py"),
            _answer("challenge_tests/../escape.py"),
            _answer(extra_key=1),
            json.dumps(
                {
                    "test_path": "challenge_tests/other.py",
                    "test_files": [{"path": "challenge_tests/t.py", "content": "pass\n"}],
                }
            ),
        )
    ):
        out = tmp_path / f"out{index}"
        result = run_rederiver(
            _bundle(tmp_path), CLAIM, out_dir=out, model_client=FakeClient(payload)
        )
        assert result is None, payload
        record = json.loads((out / "rederive.json").read_text(encoding="utf-8"))
        assert record["test_path"] is None and record["rejected"]
