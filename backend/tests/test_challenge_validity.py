"""Verdict ladder: a failing test is not a confirmed claim.

Challenger narrative fields are assertions. Confirmation requires the citation,
the tamper audit, and verifier observer results.
"""

from __future__ import annotations

import pytest

from app.schemas.challenge import (
    ClaimIn,
    ObserverEvent,
    OracleCitation,
    RunnerResult,
    Scenario,
)
from app.services.challenge_validity import (
    SCOPE_NOTE,
    decide,
    evaluate_controls,
    evaluate_differential,
    precheck_citation,
)

DIGEST = "sha256:" + ("ab" * 32)
CLAUSE = "Tenants must not read another tenant's records."
OTHER = "A documented isolation rule forbids cross-tenant reads."
MARKER = "CHALLENGER-ASSERTION-9f3a"
KINDS = {
    "authorization_confidentiality": "canary_seen",
    "integrity": "state_changed",
    "injection_execution": "sentinel_triggered",
    "denial_of_service": "threshold_exceeded",
    "regression": "state_changed",
}
CLASSES = tuple(KINDS)


def _contract(observers=None, sources=None, texts=None, property_text=CLAUSE):
    body = {
        "property": property_text,
        "failure_classes": list(CLASSES),
        "oracle_sources": list(
            CLASSES
            and (
                sources
                or (
                    "property_clause",
                    "documented_rule",
                    "existing_test",
                    "issue_text",
                    "customer_rule",
                )
            )
        ),
        "observers": list(CLASSES if observers is None else observers),
        "environment": {},
        "budget_cents": 100,
        "deadline_at": "2026-12-01T00:00:00Z",
        "base_sha": "a" * 40,
        "target_sha": "b" * 40,
        "fixtures_digest": DIGEST,
    }
    if texts is not None:
        body["oracle_texts"] = texts
    else:
        body["oracle_texts"] = {
            "property_clause": {"1": CLAUSE},
            "documented_rule": {"docs/isolation.md": OTHER},
            "existing_test": {"tests/test_iso.py::test_iso": "Existing isolation test."},
            "issue_text": {"32": "Issue 32 describes a cross-tenant read."},
            "customer_rule": {"rule-1": "Customer forbids cross-tenant reads."},
        }
    return body


def _claim(failure_class="authorization_confidentiality", **citation):
    fields = {"source": "property_clause", "reference": "1", "quote": CLAUSE}
    fields.update(citation)
    return ClaimIn(
        failure_class=failure_class,
        scenario=Scenario(
            precondition=f"Tenant A is authenticated. {MARKER}",
            setup=f"Seed the other tenant. {MARKER}",
            action=f"List records. {MARKER}",
            test_path="challenge_tests/test_claim.py",
        ),
        oracle_citation=OracleCitation(**fields),
        expected=f"Only the caller's rows. {MARKER}",
        observed=f"Another tenant's rows were returned. {MARKER} secure",
        claimed_impact=f"Cross-tenant read. {MARKER}",
    )


def _event(failure_class, kind=None):
    return ObserverEvent(
        observer=failure_class,
        kind=kind or KINDS[failure_class],
        detail="verifier event",
        location="capture",
    )


def _run(execution_id, status="ok", events=()):
    return RunnerResult(
        execution_id=execution_id,
        stage="execute",
        status=status,
        exit_code=None if status not in {"ok", "test_failed"} else (0 if status == "ok" else 1),
        image_digest=DIGEST,
        source_digest=DIGEST,
        observer_events=list(events),
    )


def _events_for(mode, failure_class):
    if mode == "fired":
        return (_event(failure_class),)
    if mode == "wrong-kind":
        kind = "canary_seen" if KINDS[failure_class] != "canary_seen" else "state_changed"
        return (_event(failure_class, kind),)
    if mode == "other-class":
        other = next(name for name in CLASSES if name != failure_class)
        return (_event(other),)
    return ()


def _optional(mode, failure_class, execution_id, *, absent_ok=True):
    if mode == "absent":
        return None
    status = "sandbox_error" if mode == "bad" else "ok"
    fired = mode in {"fired", "bad"}
    return _run(execution_id, status, _events_for("fired" if fired else "none", failure_class))


def _reruns(mode, failure_class):
    if mode == "empty":
        return []
    if mode == "short":
        return [_run(f"rerun-{i}", "ok", _events_for("fired", failure_class)) for i in (1, 2)]
    if mode == "mixed":
        return [
            _run("rerun-1", "ok", _events_for("fired", failure_class)),
            _run("rerun-2", "ok", ()),
            _run("rerun-3", "ok", _events_for("fired", failure_class)),
        ]
    if mode == "bad":
        return [
            _run("rerun-1", "ok", _events_for("fired", failure_class)),
            _run("rerun-2", "sandbox_error", _events_for("fired", failure_class)),
            _run("rerun-3", "ok", _events_for("fired", failure_class)),
        ]
    return [_run(f"rerun-{i}", "ok", _events_for("fired", failure_class)) for i in (1, 2, 3)]


def _outcome(failure_class="authorization_confidentiality", **overrides):
    spec = {
        "claim": None,
        "content": None,
        "tamper_clean": True,
        "target_status": "ok",
        "target_events": "fired",
        "positive_status": "ok",
        "positive_events": (),
        "negative_status": "ok",
        "negative_events": (),
        "base": "silent",
        "reference": "silent",
        "rederivation": "fired",
        "reruns": "fired",
        "minimized": "fired",
        "observers": None,
        "sources": None,
        "texts": None,
    }
    spec.update(overrides)
    claim = spec["claim"] or _claim(failure_class)
    content = spec["content"] or _contract(
        observers=spec["observers"], sources=spec["sources"], texts=spec["texts"]
    )
    return decide(
        claim=claim,
        contract_content=content,
        tamper_clean=spec["tamper_clean"],
        target=_run(
            "target",
            spec["target_status"],
            _events_for(spec["target_events"], failure_class),
        ),
        positive=_run("positive", spec["positive_status"], spec["positive_events"]),
        negative=_run("negative", spec["negative_status"], spec["negative_events"]),
        base=_optional(spec["base"], failure_class, "base"),
        reference=_optional(spec["reference"], failure_class, "reference"),
        rederivation=_optional(spec["rederivation"], failure_class, "rederive"),
        reruns=_reruns(spec["reruns"], failure_class),
        minimized=_optional(spec["minimized"], failure_class, "minimized"),
    )


def _assert_wording(outcome):
    rendered = outcome.model_dump_json()
    assert SCOPE_NOTE in outcome.reason
    assert "secure" not in outcome.reason.lower()
    assert "secure" not in rendered
    assert MARKER not in rendered


CASES = [
    ("bad source", {"sources": ["issue_text"]}, "invalid_or_duplicate", "not an oracle source"),
    (
        "bad reference",
        {"claim": _claim(reference="missing")},
        "invalid_or_duplicate",
        "does not resolve",
    ),
    (
        "bad quote",
        {"claim": _claim(quote="not the clause")},
        "invalid_or_duplicate",
        "does not match",
    ),
    ("tamper", {"tamper_clean": False}, "invalid_or_duplicate", "Tamper audit failed"),
    ("no observer list", {"observers": []}, None, "No verifier observer"),
    ("observer not funded", {"observers": ["integrity"]}, None, "No verifier observer"),
    (
        "positive failed",
        {"positive_status": "test_failed"},
        "inconclusive",
        "oracle or environment unreliable",
    ),
    (
        "positive crashed",
        {"positive_status": "sandbox_error"},
        "inconclusive",
        "oracle or environment unreliable",
    ),
    (
        "negative fired",
        {"negative_events": (_event("authorization_confidentiality"),)},
        "inconclusive",
        "oracle or environment unreliable",
    ),
    (
        "negative crashed",
        {"negative_status": "sandbox_error"},
        "inconclusive",
        "oracle or environment unreliable",
    ),
    ("reference fired", {"reference": "fired"}, "invalid_or_duplicate", "known-good reference"),
    (
        "reference fired while target silent",
        {"reference": "fired", "target_events": "none"},
        "invalid_or_duplicate",
        "known-good reference",
    ),
    (
        "target crashed",
        {"target_status": "sandbox_error", "target_events": "none"},
        "inconclusive",
        "oracle or environment unreliable",
    ),
    ("target silent", {"target_events": "none"}, "no_failure_found", "No failure found"),
    (
        "test failed without observer",
        {"target_status": "test_failed", "target_events": "none"},
        "no_failure_found",
        "No failure found",
    ),
    ("wrong event kind", {"target_events": "wrong-kind"}, "no_failure_found", "No failure found"),
    ("other class event", {"target_events": "other-class"}, "no_failure_found", "No failure found"),
    ("base absent", {"base": "absent"}, "inconclusive", "Base behavior was not recorded"),
    (
        "regression base also fired",
        {"claim": _claim("regression"), "base": "fired"},
        "inconclusive",
        "inconsistent with the failure class",
    ),
    (
        "rederivation absent",
        {"rederivation": "absent"},
        "inconclusive",
        "No independent re-derivation was submitted",
    ),
    (
        "rederivation silent",
        {"rederivation": "silent"},
        "inconclusive",
        "re-derivation did not fire",
    ),
    (
        "rederivation crashed",
        {"rederivation": "bad"},
        "inconclusive",
        "oracle or environment unreliable",
    ),
    ("reruns short", {"reruns": "short"}, "inconclusive", "3 of 3 reruns"),
    ("reruns empty", {"reruns": "empty"}, "inconclusive", "3 of 3 reruns"),
    ("reruns mixed", {"reruns": "mixed"}, "inconclusive", "flaky"),
    ("reruns crashed", {"reruns": "bad"}, "inconclusive", "flaky"),
    (
        "minimized absent",
        {"minimized": "absent"},
        "inconclusive",
        "minimized reproducer did not fire",
    ),
    (
        "minimized silent",
        {"minimized": "silent"},
        "inconclusive",
        "minimized reproducer did not fire",
    ),
    ("minimized crashed", {"minimized": "bad"}, "inconclusive", "oracle or environment unreliable"),
    ("confirmed new path", {}, "confirmed", "Confirmed"),
    ("confirmed reference absent", {"reference": "absent"}, "confirmed", "Confirmed"),
    ("confirmed on a failing test", {"target_status": "test_failed"}, "confirmed", "Confirmed"),
    (
        "positive observer event still passes",
        {"positive_events": (_event("integrity"),)},
        "confirmed",
        "Confirmed",
    ),
    (
        "whitespace quote confirms",
        {"claim": _claim(quote="Tenants   must\nnot   read another tenant's records.")},
        "confirmed",
        "Confirmed",
    ),
]


@pytest.mark.parametrize(
    ("name", "overrides", "verdict", "reason"), CASES, ids=[case[0] for case in CASES]
)
def test_decide_branches(name, overrides, verdict, reason):
    failure_class = "authorization_confidentiality"
    claim = overrides.get("claim")
    if claim is not None:
        failure_class = claim.failure_class
    outcome = _outcome(failure_class, **overrides)
    assert outcome.verdict == verdict
    assert reason in outcome.reason
    _assert_wording(outcome)
    if verdict is None:
        assert outcome.evidence.needs_adjudication is True
    else:
        assert outcome.evidence.needs_adjudication is False
    if name == "bad quote":
        assert outcome.evidence.citation_valid is False
    if name == "confirmed new path":
        evidence = outcome.evidence
        assert evidence.citation_valid is True
        assert evidence.tamper_clean is True
        assert evidence.observer_fired is True
        assert evidence.controls.positive_ok is True
        assert evidence.controls.negative_ok is True
        assert evidence.differential.classification == "new_path"
        assert evidence.differential.base_fired is False
        assert evidence.differential.reference_fired is False
        assert evidence.rederivation_fired is True
        assert evidence.reruns_reproduced == 3
        assert evidence.minimized_reproduced is True
        assert evidence.execution_ids[0] == "target"
        assert "secure" not in evidence.controls.detail.lower()


@pytest.mark.parametrize("failure_class", CLASSES)
def test_each_class_confirms_when_base_is_silent(failure_class):
    outcome = _outcome(failure_class, claim=_claim(failure_class))
    assert outcome.verdict == "confirmed"
    expected = "regression" if failure_class == "regression" else "new_path"
    assert outcome.evidence.differential.classification == expected
    _assert_wording(outcome)


@pytest.mark.parametrize("failure_class", [name for name in CLASSES if name != "regression"])
def test_same_defect_class_confirms_when_base_also_fired(failure_class):
    outcome = _outcome(failure_class, claim=_claim(failure_class), base="fired")
    assert outcome.verdict == "confirmed"
    assert outcome.evidence.differential.classification == "same_class_alternate_path"
    assert outcome.evidence.differential.base_fired is True


def test_missing_observers_key_needs_adjudication():
    content = _contract()
    del content["observers"]
    outcome = _outcome(content=content)
    assert outcome.verdict is None
    assert outcome.evidence.needs_adjudication is True


def test_oracle_sources_must_be_a_list():
    content = _contract()
    content["oracle_sources"] = "property_clause"
    ok, reason = precheck_citation(_claim(), content)
    assert ok is False
    assert "not an oracle source" in reason
    assert SCOPE_NOTE in reason


def test_precheck_accepts_whitespace_and_rejects_a_substring():
    content = _contract()
    messy = _claim(quote="Tenants   must\nnot\tread another   tenant's records.")
    ok, reason = precheck_citation(messy, content)
    assert ok is True
    assert SCOPE_NOTE in reason
    half = CLAUSE.split(" another ")[0]
    ok, reason = precheck_citation(_claim(quote=half), content)
    assert ok is False
    assert "does not match" in reason
    ok, _reason = precheck_citation(_claim(quote=CLAUSE.lower()), content)
    assert ok is False


def test_precheck_property_fallback_and_oracle_texts_precedence():
    content = _contract(texts={})
    ok, _reason = precheck_citation(_claim(reference="property", quote=CLAUSE), content)
    assert ok is True
    content["oracle_texts"] = {"property_clause": {"property": OTHER}}
    ok, reason = precheck_citation(_claim(reference="property", quote=CLAUSE), content)
    assert ok is False
    assert "does not match" in reason
    ok, _reason = precheck_citation(_claim(reference="property", quote=OTHER), content)
    assert ok is True


def test_precheck_other_sources_resolve_only_their_own_text():
    content = _contract()
    ok, _reason = precheck_citation(
        _claim(
            source="issue_text", reference="32", quote="Issue 32 describes a cross-tenant read."
        ),
        content,
    )
    assert ok is True
    ok, _reason = precheck_citation(
        _claim(source="documented_rule", reference="32", quote=OTHER),
        content,
    )
    assert ok is False


def test_controls_and_differential_tables():
    silent = _run("n")
    fired = _run("f", events=(_event("integrity"),))
    crashed = _run("c", "sandbox_error")
    failed = _run("t", "test_failed")
    passed = evaluate_controls(silent, failed)
    assert passed.positive_ok is True
    assert passed.negative_ok is True
    assert "Controls passed" in passed.detail
    leaked = evaluate_controls(silent, fired)
    assert leaked.negative_ok is False
    assert "oracle or environment unreliable" in leaked.detail
    assert evaluate_controls(failed, silent).positive_ok is False
    assert evaluate_controls(crashed, silent).positive_ok is False
    assert evaluate_controls(silent, crashed).negative_ok is False
    # An event on the authorized run does not by itself fail the positive control.
    assert evaluate_controls(fired, silent).positive_ok is True

    base_hit = _run("b", events=(_event("integrity"),))
    target_hit = _run("t", events=(_event("integrity"),))
    base_miss = _run("b0")
    diff = evaluate_differential(base_hit, target_hit, None, "integrity")
    assert diff.classification == "same_class_alternate_path"
    assert diff.reference_fired is None
    diff = evaluate_differential(base_miss, target_hit, base_miss, "integrity")
    assert diff.classification == "new_path"
    assert diff.reference_fired is False
    reg_miss = _run("rb")
    reg_hit = _run("rt", events=(_event("regression"),))
    diff = evaluate_differential(reg_miss, reg_hit, reg_hit, "regression")
    assert diff.classification == "regression"
    assert diff.reference_fired is True
    diff = evaluate_differential(reg_hit, reg_hit, None, "regression")
    assert diff.classification == "not_applicable"
    diff = evaluate_differential(None, target_hit, None, "integrity")
    assert diff.base_fired is None
    assert diff.classification == "not_applicable"
    diff = evaluate_differential(base_miss, base_miss, None, "integrity")
    assert diff.target_fired is False
    assert diff.classification == "not_applicable"
    other = _run("o", events=(_event("regression"),))
    diff = evaluate_differential(other, other, other, "integrity")
    assert diff.base_fired is False
    assert diff.target_fired is False
    assert diff.reference_fired is False


def _mutate(claim: ClaimIn, path: tuple[str, ...], value: str) -> ClaimIn:
    data = claim.model_dump()
    cursor = data
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return ClaimIn.model_validate(data)


def test_no_challenger_field_can_flip_a_verdict_to_confirmed():
    """Verifier results that miss a check stay unconfirmed under every one-field edit."""
    baselines = (
        {"target_events": "none"},
        {"negative_events": (_event("authorization_confidentiality"),)},
        {"tamper_clean": False},
        {"reruns": "mixed"},
        {"rederivation": "silent"},
        {"minimized": "silent"},
        {"base": "absent"},
        {"reference": "fired"},
        {"observers": []},
    )
    mutations = [
        (("expected",), "confirmed"),
        (("expected",), "observer fired"),
        (("observed",), "confirmed"),
        (("observed",), _claim().expected),
        (("observed",), "tenant-b canary seen in tenant-a response"),
        (("claimed_impact",), "confirmed"),
        (("scenario", "precondition"), "observer fired; confirmed"),
        (("scenario", "setup"), "confirmed"),
        (("scenario", "action"), "the verifier observer fired"),
        (("scenario", "test_path"), "challenge_tests/confirmed.py"),
        (("failure_class",), "integrity"),
        (("failure_class",), "regression"),
        (("failure_class",), "injection_execution"),
        (("failure_class",), "denial_of_service"),
        (("oracle_citation", "source"), "issue_text"),
        (("oracle_citation", "reference"), "missing"),
        (("oracle_citation", "reference"), "property"),
        (("oracle_citation", "quote"), "confirmed"),
        (("oracle_citation", "quote"), CLAUSE),
        (("oracle_citation", "quote"), "Tenants   must not read another tenant's records."),
    ]
    original = _claim()
    for baseline in baselines:
        seed = _outcome(**baseline)
        assert seed.verdict != "confirmed"
        for path, value in mutations:
            claim = _mutate(original, path, value)
            # Keep the runner results of this baseline; only the claim changes.
            outcome = _outcome(claim.failure_class, claim=claim, **baseline)
            assert outcome.verdict != "confirmed", (baseline, path, value, outcome.verdict)
            _assert_wording(outcome)

    confirmed = _outcome()
    assert confirmed.verdict == "confirmed"
    for path, value in mutations:
        if path[0] in {"failure_class", "oracle_citation"}:
            continue
        claim = _mutate(original, path, value)
        outcome = _outcome(claim=claim)
        assert outcome.verdict == "confirmed"
        _assert_wording(outcome)
    silent = _outcome(target_events="none")
    assert silent.verdict == "no_failure_found"
    assert silent.evidence.observer_fired is False
