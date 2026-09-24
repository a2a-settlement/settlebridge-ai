"""Versioned assurance contracts. Missing evidence is unknown, never a pass."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

AssertionStatus = Literal["pass", "fail", "unknown"]
TrialKind = Literal["scripted", "seeded", "emergent"]
Designation = Literal["pilot", "held_out"]


class ScenarioSpec(BaseModel):
    schema_version: str = SCHEMA_VERSION
    scenario_id: str
    scenario_version: str
    task_hash: str
    acceptance_criteria_hash: str
    topology: Literal["single", "team"]
    budget_units: int
    fees_count: bool = True
    collaboration_permission: Literal["prohibited", "authorized"]
    channel: Literal["off", "on", "read_only"]
    solvability: Literal["solvable", "impossible"]
    missing_prerequisite: str | None = None
    trial_kind: TrialKind = "scripted"
    control_profile: Literal["baseline", "strengthened"]
    attack: str | None = None


class ActorConfig(BaseModel):
    actor_id: str
    role: str
    provider: str | None = None
    model_id: str | None = None
    endpoint: str | None = None


class RunManifest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    scenario_id: str
    scenario_hash: str
    repository_shas: dict[str, str]
    actors: list[ActorConfig]
    policy_hash: str
    control_profile: str
    designation: Designation = "pilot"
    repeat_index: int = 0
    environment_label: str
    detector_version: str = "0"
    resource_limits: dict[str, int] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list)


class ToolEvent(BaseModel):
    actor_id: str
    tool_id: str
    arguments: dict
    status_code: int | None = None
    effect: str | None = None


class EvidenceBundle(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    events: list[ToolEvent] = Field(default_factory=list)
    ledger_before: dict = Field(default_factory=dict)
    ledger_after: dict = Field(default_factory=dict)
    acceptance_present: bool | None = None
    collector_enabled: bool = True
    telemetry_gaps: list[str] = Field(default_factory=list)
    final_reports: dict[str, str] = Field(default_factory=dict)


class AssertionResult(BaseModel):
    invariant_id: str
    status: AssertionStatus
    observed: str
    evidence_refs: list[str] = Field(default_factory=list)
    attempted: bool = False
    realized_effect: bool = False


class BehaviorLabel(BaseModel):
    label: str
    status: Literal["present", "absent", "uncertain"]
    evidence_refs: list[str] = Field(default_factory=list)


class AssessmentResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    task_completed: bool
    attempted_violations: int
    successful_violations: int
    blocks: int
    infrastructure_errors: int
    assertions: list[AssertionResult]
    behaviors: list[BehaviorLabel] = Field(default_factory=list)
    legitimate_completion: bool = False
    false_refusal: bool = False
    appropriate_escalation: bool = False
    designation: Designation = "pilot"
    conformance: Literal["PASS", "FAIL", "N/A"] | None = None
    mode: Literal["simulation", "live"] = "simulation"


def to_conformance(assertion: AssertionResult) -> Literal["PASS", "FAIL", "N/A"]:
    if assertion.status == "unknown":
        return "N/A"
    return "PASS" if assertion.status == "pass" else "FAIL"
