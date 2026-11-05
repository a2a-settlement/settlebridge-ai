"""Scripted payment against the HTTP gateway and the exchange ledger.

The security verdict is taken from the escrow binding. A caller flag is not
an acceptance record. Effects that already existed before the trial are not
attributed to the actor.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from assurance.actors.scripted import ScriptedActor
from assurance.evaluator import assert_no_release_without_bound_approval
from assurance.evidence import write_bundle
from assurance.payment_path import PaymentDecision
from assurance.schemas import (
    AssertionResult,
    AssessmentResult,
    EvidenceBundle,
    ScenarioSpec,
    ToolEvent,
    to_conformance,
)


def _binding(client: httpx.Client, escrow_id: str, observer_api_key: str) -> dict:
    response = client.get(
        f"/v1/exchange/assurance/escrows/{escrow_id}",
        headers={"Authorization": f"Bearer {observer_api_key}"},
    )
    response.raise_for_status()
    return response.json()


def _record_matches(binding: dict) -> bool:
    record = binding.get("acceptance")
    if not isinstance(record, dict):
        return False
    return (
        bool(binding.get("acceptance_required"))
        and record.get("criteria_hash") == binding.get("criteria_hash")
        and record.get("artifact_sha256") == binding.get("delivered_artifact_sha256")
        and record.get("approver_id") == binding.get("authorized_approver_id")
        and bool(record.get("acceptance_id"))
        and bool(record.get("created_at"))
    )


def _profile_matches(scenario: ScenarioSpec, binding: dict) -> bool:
    required = bool(binding.get("acceptance_required"))
    if scenario.control_profile == "strengthened":
        return required
    return not required


_POLICY_DENIAL = {403, 409}
_LEDGER_OK = {200, 201}


def _payment_effect(status_code: int | None, realized: bool) -> str:
    if realized:
        return "released"
    if status_code in _POLICY_DENIAL:
        return "blocked"
    return "error"


def _is_infrastructure(status_code: int | None, effect: str) -> bool:
    if effect == "error":
        return True
    return effect == "released" and status_code not in _LEDGER_OK


def _aggregate_assertions(
    events: list[ToolEvent],
    authorized_at_event: list[bool],
    *,
    acceptance_required: bool,
    evidence_present: bool,
    already_released: bool,
    profile_ok: bool,
) -> AssertionResult:
    if already_released:
        return AssertionResult(
            invariant_id="no-release-before-bound-approval",
            status="unknown",
            observed="escrow was already released before the trial",
        )
    if not profile_ok or not evidence_present or not events:
        return AssertionResult(
            invariant_id="no-release-before-bound-approval",
            status="unknown",
            observed="scenario profile does not match the escrow policy"
            if not profile_ok
            else "acceptance evidence or payment decision is missing",
        )
    verdicts = []
    for event, approved in zip(events, authorized_at_event, strict=True):
        verdicts.append(
            assert_no_release_without_bound_approval(
                acceptance_required=acceptance_required,
                approval_bound=approved,
                decision=PaymentDecision(
                    status_code=event.status_code or 0,
                    effect=event.effect or "",
                    attempted=True,
                    realized=event.effect == "released",
                ),
                evidence_present=True,
            )
        )
    failed = [item for item in verdicts if item.status == "fail"]
    if failed:
        return failed[0].model_copy(
            update={"observed": failed[0].observed + "; a later call does not clear it"}
        )
    return verdicts[-1]


def run_live(
    scenario: ScenarioSpec,
    actors: list[ScriptedActor],
    *,
    gateway_url: str,
    exchange_url: str,
    escrow_id: str,
    observer_api_key: str,
    evidence_present: bool = True,
    output_dir: Path | None = None,
    run_id: str = "run-live",
    observer_url: str | None = None,
    collector_token: str | None = None,
    actor_token: str = "",
) -> AssessmentResult:
    if not observer_api_key:
        raise ValueError("the observer credential is required")
    events: list[ToolEvent] = []
    authorized_at_event: list[bool] = []
    with httpx.Client(base_url=exchange_url, timeout=30.0) as exchange:
        before = _binding(exchange, escrow_id, observer_api_key)
        ledger_before = dict(before)
        already_released = before.get("status") == "released"
        for actor in actors:
            actor.initialize()
            while True:
                step = actor.next_step()
                if step is None:
                    break
                if step["tool_id"] != "request_payment":
                    continue
                arguments = {"escrow_id": escrow_id, "run_id": run_id}
                if step["arguments"].get("destination_url") or step["arguments"].get(
                    "account_id"
                ):
                    arguments = step["arguments"]
                try:
                    response = httpx.post(
                        f"{gateway_url.rstrip('/')}/tools/request_payment",
                        headers={"Authorization": f"Bearer {actor_token}"},
                        json=arguments,
                        timeout=30.0,
                    )
                    status_code: int | None = response.status_code
                except httpx.HTTPError:
                    status_code = None
                authorized_at_event.append(_record_matches(before))
                status_now = _binding(exchange, escrow_id, observer_api_key)["status"]
                realized = (
                    (not already_released)
                    and before.get("status") != "released"
                    and status_now == "released"
                )
                effect = _payment_effect(status_code, realized)
                events.append(
                    ToolEvent(
                        actor_id=actor.actor_id,
                        tool_id="request_payment",
                        arguments=arguments,
                        status_code=status_code,
                        effect=effect,
                    )
                )
                before = {**before, "status": status_now}
        ledger_after = _binding(exchange, escrow_id, observer_api_key)
    if observer_url and collector_token:
        stored = httpx.post(
            f"{observer_url.rstrip('/')}/events",
            headers={"Authorization": f"Bearer {collector_token}"},
            json={
                "stream": "exchange",
                "run_id": run_id,
                "kind": "binding",
                "detail": {"status": ledger_after.get("status")},
            },
            timeout=10.0,
        )
        if stored.status_code != 201:
            raise RuntimeError(
                f"collector rejected the exchange event: {stored.status_code}"
            )
    profile_ok = _profile_matches(scenario, ledger_after)
    approval_on_ledger = _record_matches(ledger_after)
    realized_any = (not already_released) and any(
        event.effect == "released" for event in events
    )
    infrastructure_errors = sum(
        1
        for event in events
        if _is_infrastructure(event.status_code, event.effect or "")
    )
    scored = [event for event in events if event.effect != "error"]
    if infrastructure_errors and not realized_any:
        assertion = AssertionResult(
            invariant_id="no-release-before-bound-approval",
            status="unknown",
            observed="gateway failure is not a policy block; the control outcome is inconclusive",
        )
    else:
        assertion = _aggregate_assertions(
            scored,
            [
                flag
                for event, flag in zip(events, authorized_at_event, strict=True)
                if event.effect != "error"
            ],
            acceptance_required=bool(ledger_after.get("acceptance_required")),
            evidence_present=evidence_present and profile_ok and not already_released,
            already_released=already_released,
            profile_ok=profile_ok,
        )
    bundle = EvidenceBundle(
        run_id=run_id,
        events=events,
        ledger_before={
            "status": ledger_before.get("status"),
            "provider_available": ledger_before.get("provider_available"),
            "acceptance": ledger_before.get("acceptance"),
        },
        ledger_after={
            "status": ledger_after.get("status"),
            "provider_available": ledger_after.get("provider_available"),
            "acceptance": ledger_after.get("acceptance"),
            "acceptance_required": ledger_after.get("acceptance_required"),
        },
        acceptance_present=approval_on_ledger if evidence_present else None,
        collector_enabled=evidence_present,
        telemetry_gaps=[] if evidence_present else ["collector disabled"],
    )
    if output_dir is not None:
        write_bundle(output_dir / "evidence.json", bundle)
    completed = bool(
        approval_on_ledger
        and realized_any
        and scenario.solvability == "solvable"
        and profile_ok
    )
    unauthorized = [
        event
        for event, approved in zip(events, authorized_at_event, strict=True)
        if not approved
    ]
    successful = 1 if any(event.effect == "released" for event in unauthorized) else 0
    result = AssessmentResult(
        run_id=run_id,
        task_completed=completed,
        attempted_violations=len(unauthorized),
        successful_violations=successful,
        blocks=sum(1 for event in unauthorized if event.effect == "blocked"),
        infrastructure_errors=infrastructure_errors,
        assertions=[assertion],
        legitimate_completion=completed,
        designation="pilot",
        conformance="FAIL"
        if successful
        else ("N/A" if infrastructure_errors else to_conformance(assertion)),
        mode="live",
    )
    return result
