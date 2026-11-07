"""Finding validity (owner: P1-V). Plan section 4 is the specification.

A claim is ``confirmed`` only when every check passes, and no check depends on the
challenger's own assertion. Pure functions take recorded runner results; this module
never starts a sandbox itself.

Citation text lives in the frozen contract. ``oracle_sources`` lists the funded
sources. ``oracle_texts`` maps each source to ``{reference: clause text}``. A
``property_clause`` citation whose reference is ``property`` may instead quote
``contract_content["property"]`` when that reference is absent from ``oracle_texts``.
The quote must equal the clause after whitespace is collapsed. ``observers`` lists
the funded observer names; a failure class missing from that list is sent to
adjudication.

``scenario``, ``expected``, ``observed``, and ``claimed_impact`` are never read.
A runner status of ``test_failed`` is not evidence. An observer fired only when the
result carries an event whose observer is the failure class and whose kind is the
kind that class's verifier observer emits.
"""

from __future__ import annotations

from app.schemas.challenge import (
    ClaimIn,
    ControlOutcome,
    DifferentialOutcome,
    ObserverEvent,
    RunnerResult,
    ValidationEvidence,
    ValidationOutcome,
)

SCOPE_NOTE = "Under the stated property, environment, and budget."
ORACLE_TEXT_FIELD = "oracle_texts"

_ORACLE_SOURCES = frozenset(
    {
        "property_clause",
        "documented_rule",
        "existing_test",
        "issue_text",
        "customer_rule",
    }
)
_OBSERVERS = frozenset(
    {
        "authorization_confidentiality",
        "integrity",
        "injection_execution",
        "denial_of_service",
        "regression",
    }
)
_EVENT_KIND = {
    "authorization_confidentiality": "canary_seen",
    "integrity": "state_changed",
    "injection_execution": "sentinel_triggered",
    "denial_of_service": "threshold_exceeded",
    "regression": "state_changed",
}
_COMPLETED = frozenset({"ok", "test_failed"})
_INCOMPLETE = _OBSERVERS - {"regression"}


def _scope(text: str) -> str:
    cleaned = " ".join(text.split()).rstrip(".")
    return f"{cleaned}. {SCOPE_NOTE}"


def _names(content: dict, field: str, allowed: frozenset[str]) -> set[str]:
    if not isinstance(content, dict):
        return set()
    raw = content.get(field)
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str) and item in allowed}


def _clause_text(content: dict, source: str, reference: str) -> str | None:
    if not isinstance(content, dict):
        return None
    texts = content.get(ORACLE_TEXT_FIELD)
    if isinstance(texts, dict):
        bucket = texts.get(source)
        if isinstance(bucket, dict):
            value = bucket.get(reference)
            if isinstance(value, str) and value.strip():
                return value
    if source == "property_clause" and reference == "property":
        value = content.get("property")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _normalize(text: str) -> str:
    return " ".join(text.split())


def precheck_citation(claim: ClaimIn, contract_content: dict) -> tuple[bool, str]:
    """The citation must name an oracle source listed in the frozen contract."""
    citation = claim.oracle_citation
    if citation.source not in _names(contract_content, "oracle_sources", _ORACLE_SOURCES):
        return False, _scope("Citation source is not an oracle source on the frozen contract")
    clause = _clause_text(contract_content, citation.source, citation.reference)
    if clause is None:
        return False, _scope("Citation reference does not resolve in the frozen contract")
    if _normalize(citation.quote) != _normalize(clause):
        return False, _scope("Citation quote does not match the frozen clause text")
    return True, _scope("Citation matches the frozen contract")


def _matching(result: RunnerResult | None, failure_class: str) -> list[ObserverEvent]:
    if result is None:
        return []
    kind = _EVENT_KIND.get(failure_class)
    if kind is None:
        return []
    return [
        event
        for event in result.observer_events
        if event.observer == failure_class and event.kind == kind
    ]


def _fired(result: RunnerResult | None, failure_class: str) -> bool:
    return bool(_matching(result, failure_class))


def evaluate_controls(positive: RunnerResult, negative: RunnerResult) -> ControlOutcome:
    """Positive must succeed. Negative must finish without an observer event."""
    positive_ok = positive.status == "ok"
    negative_ok = negative.status in _COMPLETED and not negative.observer_events
    if positive_ok and negative_ok:
        detail = _scope("Controls passed")
    else:
        detail = _scope("oracle or environment unreliable")
    return ControlOutcome(positive_ok=positive_ok, negative_ok=negative_ok, detail=detail)


def evaluate_differential(
    base: RunnerResult | None,
    target: RunnerResult,
    reference: RunnerResult | None,
    failure_class: str,
) -> DifferentialOutcome:
    """Record where the class observer fired and classify the target gap."""
    base_fired = None if base is None else _fired(base, failure_class)
    target_fired = _fired(target, failure_class)
    reference_fired = None if reference is None else _fired(reference, failure_class)
    if failure_class == "regression" and target_fired and base_fired is False:
        classification = "regression"
    elif failure_class in _INCOMPLETE and target_fired and base_fired is True:
        classification = "same_class_alternate_path"
    elif failure_class in _INCOMPLETE and target_fired and base_fired is False:
        classification = "new_path"
    else:
        classification = "not_applicable"
    return DifferentialOutcome(
        base_fired=base_fired,
        target_fired=target_fired,
        reference_fired=reference_fired,
        classification=classification,
    )


def _consistent(failure_class: str, differential: DifferentialOutcome) -> bool:
    if failure_class == "regression":
        return differential.classification == "regression"
    if failure_class in _INCOMPLETE:
        return differential.classification in {"same_class_alternate_path", "new_path"}
    return False


def _reproduced(result: RunnerResult | None, failure_class: str) -> bool | None:
    if result is None:
        return None
    return result.status in _COMPLETED and _fired(result, failure_class)


def _rerun_problem(reruns: list[RunnerResult], failure_class: str) -> str | None:
    if len(reruns) != 3:
        return "Repeatability requires 3 of 3 reruns"
    for result in reruns:
        if result.status not in _COMPLETED or not _fired(result, failure_class):
            return "A rerun was flaky"
    return None


def _execution_ids(
    target: RunnerResult,
    positive: RunnerResult,
    negative: RunnerResult,
    base: RunnerResult | None,
    reference: RunnerResult | None,
    rederivation: RunnerResult | None,
    reruns: list[RunnerResult],
    minimized: RunnerResult | None,
) -> list[str]:
    ids: list[str] = []
    for result in (target, positive, negative, base, reference, rederivation, *reruns, minimized):
        if result is not None:
            ids.append(result.execution_id)
    return ids


def _verdict(
    *,
    citation_ok: bool,
    citation_reason: str,
    tamper_clean: bool,
    has_observer: bool,
    controls: ControlOutcome,
    failure_class: str,
    target: RunnerResult,
    base: RunnerResult | None,
    reference: RunnerResult | None,
    differential: DifferentialOutcome,
    rederivation: RunnerResult | None,
    reruns: list[RunnerResult],
    minimized: RunnerResult | None,
) -> tuple[str | None, str, bool]:
    if not citation_ok:
        return "invalid_or_duplicate", citation_reason, False
    if not tamper_clean:
        return "invalid_or_duplicate", _scope("Tamper audit failed"), False
    if not has_observer:
        return None, _scope("No verifier observer is funded for this failure class"), True
    if not controls.positive_ok or not controls.negative_ok:
        return "inconclusive", controls.detail, False
    if reference is not None and _fired(reference, failure_class):
        return (
            "invalid_or_duplicate",
            _scope("The observer fired on a known-good reference"),
            False,
        )
    if target.status not in _COMPLETED:
        return "inconclusive", _scope("oracle or environment unreliable"), False
    if not differential.target_fired:
        return "no_failure_found", _scope("No failure found"), False
    if base is None:
        return "inconclusive", _scope("Base behavior was not recorded"), False
    if not _consistent(failure_class, differential):
        return (
            "inconclusive",
            _scope("The differential is inconsistent with the failure class"),
            False,
        )
    if rederivation is None or not _fired(rederivation, failure_class):
        return "inconclusive", _scope("Independent re-derivation did not fire"), False
    if rederivation.status not in _COMPLETED:
        return "inconclusive", _scope("oracle or environment unreliable"), False
    rerun_reason = _rerun_problem(reruns, failure_class)
    if rerun_reason is not None:
        return "inconclusive", _scope(rerun_reason), False
    if minimized is None or not _fired(minimized, failure_class):
        return "inconclusive", _scope("The minimized reproducer did not fire"), False
    if minimized.status not in _COMPLETED:
        return "inconclusive", _scope("oracle or environment unreliable"), False
    return "confirmed", _scope("Confirmed"), False


def decide(
    *,
    claim: ClaimIn,
    contract_content: dict,
    tamper_clean: bool,
    target: RunnerResult,
    positive: RunnerResult,
    negative: RunnerResult,
    base: RunnerResult | None,
    reference: RunnerResult | None,
    rederivation: RunnerResult | None,
    reruns: list[RunnerResult],
    minimized: RunnerResult | None,
) -> ValidationOutcome:
    citation_ok, citation_reason = precheck_citation(claim, contract_content)
    failure_class = claim.failure_class
    controls = evaluate_controls(positive, negative)
    differential = evaluate_differential(base, target, reference, failure_class)
    verdict, reason, needs_adjudication = _verdict(
        citation_ok=citation_ok,
        citation_reason=citation_reason,
        tamper_clean=tamper_clean,
        has_observer=failure_class in _names(contract_content, "observers", _OBSERVERS),
        controls=controls,
        failure_class=failure_class,
        target=target,
        base=base,
        reference=reference,
        differential=differential,
        rederivation=rederivation,
        reruns=reruns,
        minimized=minimized,
    )
    reproduced = sum(1 for result in reruns if _reproduced(result, failure_class))
    evidence = ValidationEvidence(
        citation_valid=citation_ok,
        tamper_clean=tamper_clean,
        observer_fired=differential.target_fired,
        observer_events=_matching(target, failure_class),
        controls=controls,
        differential=differential,
        rederivation_fired=_reproduced(rederivation, failure_class),
        reruns_reproduced=min(3, reproduced),
        minimized_reproduced=_reproduced(minimized, failure_class),
        execution_ids=_execution_ids(
            target, positive, negative, base, reference, rederivation, reruns, minimized
        ),
        needs_adjudication=needs_adjudication,
    )
    return ValidationOutcome(verdict=verdict, reason=reason, evidence=evidence)
