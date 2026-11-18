"""Verdict-rule entry points of the private-verifier client.

The rule itself is not in this repository and is not restated here. A fake
package stands in for it: the fakes record what they were given and return a
canned answer, so these tests cover only the boundary — model conversion in
both directions, and "not decided" whenever the rule cannot answer.
"""

from __future__ import annotations

import importlib.abc
import logging
import sys
import types

import pytest
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.schemas.challenge import (
    ClaimIn,
    ControlOutcome,
    ObserverEvent,
    OracleCitation,
    RunnerResult,
    Scenario,
    ValidationEvidence,
    ValidationOutcome,
)
from app.services import verifier_client
from app.services.challenge_orchestrator import _reason
from app.services.verifier_client import (
    RULE_UNAVAILABLE,
    CitationCheck,
    decide,
    precheck_citation,
    validity_available,
)

_DIGEST = "sha256:" + ("ab" * 32)
_MARKER = "rule-marker-5d1e"
_CONTRACT = {"property": "stated property", "nested": {"items": ["a", "b"]}}


class _PrivateModel(BaseModel):
    """Stands in for a private model: same data, a different class."""

    model_config = ConfigDict(extra="allow")


class _PrivateClaim(_PrivateModel):
    pass


class _PrivateResult(_PrivateModel):
    pass


class _PrivateOutcome(_PrivateModel):
    pass


@pytest.fixture(autouse=True)
def _reset_import_cache():
    verifier_client._cached = False
    yield
    verifier_client._cached = False


def _drop_verifier_modules(monkeypatch) -> None:
    for name in list(sys.modules):
        if name == "settlebridge_verifier" or name.startswith("settlebridge_verifier."):
            monkeypatch.delitem(sys.modules, name, raising=False)


class _BlockVerifier(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "settlebridge_verifier" or fullname.startswith("settlebridge_verifier."):
            raise ModuleNotFoundError(fullname)
        return None


def _hide_verifier(monkeypatch) -> None:
    _drop_verifier_modules(monkeypatch)
    monkeypatch.setattr(sys, "meta_path", [_BlockVerifier(), *sys.meta_path])


def _install(monkeypatch, *, precheck_fn=None, decide_fn=None, with_validity=True) -> None:
    _drop_verifier_modules(monkeypatch)
    package = types.ModuleType("settlebridge_verifier")
    package.__path__ = []
    schemas = types.ModuleType("settlebridge_verifier.schemas")
    schemas.ClaimIn = _PrivateClaim
    schemas.RunnerResult = _PrivateResult
    monkeypatch.setitem(sys.modules, "settlebridge_verifier", package)
    monkeypatch.setitem(sys.modules, "settlebridge_verifier.schemas", schemas)
    if with_validity:
        validity = types.ModuleType("settlebridge_verifier.validity")
        if precheck_fn is not None:
            validity.precheck_citation = precheck_fn
        if decide_fn is not None:
            validity.decide = decide_fn
        monkeypatch.setitem(sys.modules, "settlebridge_verifier.validity", validity)
    # Anything not injected above must not come from a real install.
    monkeypatch.setattr(sys, "meta_path", [_BlockVerifier(), *sys.meta_path])


def _claim() -> ClaimIn:
    return ClaimIn(
        failure_class="integrity",
        scenario=Scenario(
            precondition="p", setup="s", action="a", test_path="challenge_tests/test_gap.py"
        ),
        oracle_citation=OracleCitation(
            source="property_clause", reference="property", quote="stated property"
        ),
        expected="e",
        observed="o",
        claimed_impact="impact",
    )


def _result(purpose: str) -> RunnerResult:
    return RunnerResult(
        execution_id=f"exec:task:{purpose}:1",
        stage="execute",
        status="ok",
        exit_code=0,
        cpu_seconds=2,
        wall_seconds=3,
        image_digest=_DIGEST,
        source_digest=_DIGEST,
        observer_events=[
            ObserverEvent(observer="integrity", kind="state_changed", detail="d", location="l")
        ],
        log_digest=_DIGEST,
    )


def _inputs(**overrides) -> dict:
    body = {
        "claim": _claim(),
        "contract_content": _CONTRACT,
        "tamper_clean": True,
        "target": _result("target"),
        "positive": _result("positive"),
        "negative": _result("negative"),
        "base": _result("base"),
        "reference": None,
        "rederivation": _result("rederive"),
        "reruns": [_result("rerun-1"), _result("rerun-2"), _result("rerun-3")],
        "minimized": _result("minimized"),
    }
    body.update(overrides)
    return body


def _canned_outcome(verdict="confirmed", **evidence) -> dict:
    body = {
        "citation_valid": True,
        "tamper_clean": True,
        "observer_fired": True,
        "observer_events": [
            {"observer": "integrity", "kind": "state_changed", "detail": "d", "location": "l"}
        ],
        "controls": {"positive_ok": True, "negative_ok": True, "detail": "canned"},
        "differential": {
            "base_fired": False,
            "target_fired": True,
            "reference_fired": None,
            "classification": "new_path",
        },
        "rederivation_fired": True,
        "reruns_reproduced": 3,
        "minimized_reproduced": True,
        "execution_ids": ["exec:task:target:1"],
        "needs_adjudication": False,
    }
    body.update(evidence)
    return {"verdict": verdict, "reason": "canned reason", "evidence": body}


def _assert_not_decided(outcome: ValidationOutcome, inputs: dict) -> None:
    assert isinstance(outcome, ValidationOutcome)
    assert outcome.verdict == "inconclusive"
    assert outcome.reason == RULE_UNAVAILABLE
    assert outcome.evidence.citation_valid is False
    assert outcome.evidence.observer_fired is False
    assert outcome.evidence.needs_adjudication is False
    assert outcome.evidence.tamper_clean is inputs["tamper_clean"]
    supplied = [
        inputs["target"],
        inputs["positive"],
        inputs["negative"],
        inputs["base"],
        inputs["reference"],
        inputs["rederivation"],
        *inputs["reruns"],
        inputs["minimized"],
    ]
    assert sorted(outcome.evidence.execution_ids) == sorted(
        item.execution_id for item in supplied if item is not None
    )


# ---------------------------------------------------------------- conversion, inbound


def test_precheck_hands_the_rule_a_private_claim_and_a_copy_of_the_contract(monkeypatch):
    seen = {}

    def precheck(claim, content):
        seen["claim"] = claim
        seen["content"] = content
        content["nested"]["items"].append("mutated by the rule")
        return True, "canned"

    _install(monkeypatch, precheck_fn=precheck, decide_fn=lambda **kw: None)
    public = _claim()

    assert precheck_citation(public, _CONTRACT) == CitationCheck("valid", "canned")

    assert type(seen["claim"]) is _PrivateClaim
    assert seen["claim"].model_dump(mode="json") == public.model_dump(mode="json")
    assert seen["content"]["property"] == "stated property"
    assert _CONTRACT["nested"]["items"] == ["a", "b"]


def test_decide_hands_the_rule_private_models_with_the_same_data(monkeypatch):
    seen = {}

    def rule(**kwargs):
        seen.update(kwargs)
        return _PrivateOutcome(**_canned_outcome())

    _install(monkeypatch, precheck_fn=lambda c, d: (True, ""), decide_fn=rule)
    inputs = _inputs(tamper_clean=False)

    decide(**inputs)

    assert set(seen) == set(inputs)
    assert type(seen["claim"]) is _PrivateClaim
    assert seen["claim"].model_dump(mode="json") == inputs["claim"].model_dump(mode="json")
    assert seen["tamper_clean"] is False
    assert seen["contract_content"] == _CONTRACT
    assert seen["contract_content"] is not _CONTRACT
    for name in ("target", "positive", "negative", "base", "rederivation", "minimized"):
        assert type(seen[name]) is _PrivateResult, name
        assert seen[name].model_dump(mode="json") == inputs[name].model_dump(mode="json")
    assert seen["reference"] is None
    assert [type(item) for item in seen["reruns"]] == [_PrivateResult] * 3
    assert [item.execution_id for item in seen["reruns"]] == [
        item.execution_id for item in inputs["reruns"]
    ]


def test_absent_optional_results_reach_the_rule_as_none(monkeypatch):
    seen = {}

    def rule(**kwargs):
        seen.update(kwargs)
        return _canned_outcome()

    _install(monkeypatch, precheck_fn=lambda c, d: (True, ""), decide_fn=rule)

    decide(**_inputs(base=None, reference=None, rederivation=None, minimized=None, reruns=[]))

    assert seen["base"] is None
    assert seen["reference"] is None
    assert seen["rederivation"] is None
    assert seen["minimized"] is None
    assert seen["reruns"] == []


# ---------------------------------------------------------------- conversion, outbound


@pytest.mark.parametrize("wrap", [lambda body: _PrivateOutcome(**body), lambda body: body])
def test_the_rules_outcome_comes_back_as_the_public_model(monkeypatch, wrap):
    canned = _canned_outcome()
    _install(monkeypatch, precheck_fn=lambda c, d: (True, ""), decide_fn=lambda **kw: wrap(canned))

    outcome = decide(**_inputs())

    assert type(outcome) is ValidationOutcome
    assert type(outcome.evidence) is ValidationEvidence
    assert type(outcome.evidence.controls) is ControlOutcome
    assert type(outcome.evidence.observer_events[0]) is ObserverEvent
    assert outcome.model_dump(mode="json") == canned


@pytest.mark.parametrize(
    "verdict", ["confirmed", "no_failure_found", "inconclusive", "invalid_or_duplicate"]
)
def test_each_verdict_the_rule_gives_is_passed_through_unchanged(monkeypatch, verdict):
    canned = _canned_outcome(verdict=verdict)
    _install(monkeypatch, precheck_fn=lambda c, d: (True, ""), decide_fn=lambda **kw: canned)

    outcome = decide(**_inputs())

    assert outcome.verdict == verdict
    assert outcome.reason == "canned reason"


def test_an_adjudication_outcome_is_passed_through(monkeypatch):
    canned = _canned_outcome(verdict=None, needs_adjudication=True)
    _install(monkeypatch, precheck_fn=lambda c, d: (True, ""), decide_fn=lambda **kw: canned)

    outcome = decide(**_inputs())

    assert outcome.verdict is None
    assert outcome.evidence.needs_adjudication is True


@pytest.mark.parametrize(
    "answer,status", [((True, "holds"), "valid"), ((False, "does not hold"), "invalid")]
)
def test_precheck_answers_map_to_a_status(monkeypatch, answer, status):
    _install(monkeypatch, precheck_fn=lambda c, d: answer, decide_fn=lambda **kw: None)

    assert precheck_citation(_claim(), _CONTRACT) == CitationCheck(status, answer[1])


# ---------------------------------------------------------------- fail closed


def test_missing_package_is_not_decided(monkeypatch):
    _hide_verifier(monkeypatch)
    inputs = _inputs()

    assert validity_available() is False
    assert precheck_citation(_claim(), _CONTRACT) == CitationCheck("unavailable", RULE_UNAVAILABLE)
    _assert_not_decided(decide(**inputs), inputs)


def test_package_without_the_rule_module_is_not_decided(monkeypatch):
    _install(monkeypatch, with_validity=False)
    inputs = _inputs()

    assert validity_available() is False
    assert precheck_citation(_claim(), _CONTRACT).status == "unavailable"
    _assert_not_decided(decide(**inputs), inputs)


def test_rule_module_missing_an_entry_point_is_not_decided(monkeypatch):
    _install(monkeypatch, precheck_fn=lambda c, d: (True, "canned"))
    inputs = _inputs()

    assert validity_available() is False
    assert precheck_citation(_claim(), _CONTRACT).status == "unavailable"
    _assert_not_decided(decide(**inputs), inputs)


def test_an_exception_from_the_rule_is_not_decided_and_not_logged_verbatim(monkeypatch, caplog):
    def boom(*_args, **_kwargs):
        raise RuntimeError(_MARKER)

    _install(monkeypatch, precheck_fn=boom, decide_fn=boom)
    inputs = _inputs(tamper_clean=False)
    caplog.set_level(logging.DEBUG)

    assert validity_available() is True
    check = precheck_citation(_claim(), _CONTRACT)
    outcome = decide(**inputs)

    assert check == CitationCheck("unavailable", RULE_UNAVAILABLE)
    _assert_not_decided(outcome, inputs)
    assert _MARKER not in caplog.text
    assert "RuntimeError" in caplog.text


def test_a_claim_the_private_model_refuses_is_not_decided(monkeypatch):
    class _Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")

    _install(
        monkeypatch,
        precheck_fn=lambda c, d: (True, "canned"),
        decide_fn=lambda **kw: _canned_outcome(),
    )
    monkeypatch.setattr(sys.modules["settlebridge_verifier.schemas"], "ClaimIn", _Strict)
    inputs = _inputs()

    assert precheck_citation(_claim(), _CONTRACT).status == "unavailable"
    _assert_not_decided(decide(**inputs), inputs)


@pytest.mark.parametrize(
    "answer",
    [
        None,
        True,
        "valid",
        (True,),
        (True, "ok", "extra"),
        ("true", "ok"),
        (1, "ok"),
        (True, None),
        [True, "ok"],
        {"ok": True},
    ],
)
def test_a_malformed_precheck_answer_is_unavailable_never_valid(monkeypatch, answer):
    _install(monkeypatch, precheck_fn=lambda c, d: answer, decide_fn=lambda **kw: None)

    assert precheck_citation(_claim(), _CONTRACT) == CitationCheck("unavailable", RULE_UNAVAILABLE)


def _without(body: dict, key: str) -> dict:
    return {name: value for name, value in body.items() if name != key}


@pytest.mark.parametrize(
    "answer",
    [
        None,
        "no_failure_found",
        ("no_failure_found", "reason"),
        {},
        _canned_outcome(verdict="verified"),
        _canned_outcome(verdict="NO_FAILURE_FOUND"),
        _without(_canned_outcome(verdict="no_failure_found"), "evidence"),
        _without(_canned_outcome(verdict="no_failure_found"), "reason"),
        _without(_canned_outcome(verdict="no_failure_found"), "verdict"),
        {**_canned_outcome(verdict="no_failure_found"), "unexpected": 1},
        _canned_outcome(verdict="no_failure_found", reruns_reproduced=4),
        _canned_outcome(verdict="no_failure_found", citation_valid="maybe"),
        _canned_outcome(verdict="no_failure_found", unexpected_evidence=True),
    ],
)
def test_a_malformed_outcome_is_not_decided(monkeypatch, answer):
    _install(monkeypatch, precheck_fn=lambda c, d: (True, ""), decide_fn=lambda **kw: answer)
    inputs = _inputs()

    _assert_not_decided(decide(**inputs), inputs)


def test_a_malformed_private_model_is_not_decided(monkeypatch):
    answer = _PrivateOutcome(verdict="no_failure_found", reason="canned reason")
    _install(monkeypatch, precheck_fn=lambda c, d: (True, ""), decide_fn=lambda **kw: answer)
    inputs = _inputs()

    _assert_not_decided(decide(**inputs), inputs)


# ---------------------------------------------------------------- wiring


def test_the_rule_is_not_gated_by_the_fix_verifier_flag(monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_ENABLED", False)
    _install(
        monkeypatch,
        precheck_fn=lambda c, d: (True, "canned"),
        decide_fn=lambda **kw: _canned_outcome(),
    )

    assert validity_available() is True
    assert precheck_citation(_claim(), _CONTRACT).status == "valid"
    assert decide(**_inputs()).verdict == "confirmed"


def test_a_package_installed_after_a_miss_is_picked_up(monkeypatch):
    _hide_verifier(monkeypatch)
    assert validity_available() is False

    _install(
        monkeypatch,
        precheck_fn=lambda c, d: (True, "canned"),
        decide_fn=lambda **kw: _canned_outcome(),
    )

    assert validity_available() is True


def test_the_fallback_reason_is_one_the_orchestrator_can_store():
    assert _reason(RULE_UNAVAILABLE) == RULE_UNAVAILABLE
    assert not RULE_UNAVAILABLE.endswith(".")
