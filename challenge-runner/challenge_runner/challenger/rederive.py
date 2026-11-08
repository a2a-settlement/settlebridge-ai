"""Independent re-deriver.

Sees the frozen bundle and the claim's failure class, scenario and citation. It never
sees the challenger's test files, expected or observed behaviour, or claimed impact.
It writes one pytest bundle under ``challenge_tests/`` for the ``rederive`` stage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from challenge_runner.challenger import ChallengeBundle, ModelClient
from challenge_runner.challenger.limits import MAX_COMPLETION_TOKENS, MAX_MODEL_CHARS
from challenge_runner.challenger.output import (
    model_tokens_from_client,
    package_bundle,
    prepare_output,
    store_test_files,
    write_json,
)
from challenge_runner.challenger.parse import _test_files, _unwrap
from challenge_runner.challenger.paths import normalize_test_path
from challenge_runner.challenger.prompt import build_rederive_prompts

_KEYS = frozenset({"test_path", "test_files"})


@dataclass(frozen=True)
class Rederivation:
    test_path: str
    bundle_path: Path


def run_rederiver(
    bundle: ChallengeBundle, claim: dict, *, out_dir: Path, model_client: ModelClient
) -> Rederivation | None:
    root = prepare_output(out_dir)
    system, prompt = build_rederive_prompts(
        contract=bundle.contract,
        source_dir=bundle.source_dir,
        diff_text=bundle.diff_text,
        failure_class=claim.get("failure_class"),
        scenario=claim.get("scenario") or {},
        oracle_citation=claim.get("oracle_citation") or {},
    )
    try:
        raw = model_client.complete(system=system, prompt=prompt, max_tokens=MAX_COMPLETION_TOKENS)
    finally:
        write_json(
            root / "usage.json",
            {"calls": 1, "model_tokens": model_tokens_from_client(model_client)},
        )
    test_path, files, reason = _parse(raw)
    if reason is None:
        _stored, rejected = store_test_files(root, [{"test_files": files}])
        if rejected["count"]:
            reason = next(iter(rejected["reasons"]))
    write_json(
        root / "rederive.json",
        {"test_path": test_path if reason is None else None, "rejected": reason},
    )
    if reason is not None:
        return None
    return Rederivation(test_path=test_path, bundle_path=package_bundle(root))


def _parse(text: object) -> tuple[str | None, list[dict], str | None]:
    if not isinstance(text, str):
        return None, [], "not_json"
    if len(text) > MAX_MODEL_CHARS:
        return None, [], "oversized_output"
    try:
        data = json.loads(_unwrap(text))
    except json.JSONDecodeError:
        return None, [], "not_json"
    if not isinstance(data, dict):
        return None, [], "not_an_object"
    if set(data) != _KEYS:
        return None, [], "missing_field" if (_KEYS - set(data)) else "unexpected_field"
    test_path = normalize_test_path(data.get("test_path"))
    if test_path is None:
        return None, [], "unsafe_test_path"
    files, error = _test_files(data.get("test_files"))
    if error:
        return None, [], error
    if test_path not in {path for path, _blob in files}:
        return None, [], "test_path_mismatch"
    return (
        test_path,
        [{"path": path, "content": blob.decode("utf-8")} for path, blob in files],
        None,
    )
