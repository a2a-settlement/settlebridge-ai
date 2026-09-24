"""Scripted actors call tool ids. They do not hold exchange credentials."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScriptedActor:
    actor_id: str
    role: str
    steps: list[dict] = field(default_factory=list)
    stopped: bool = False

    def initialize(self) -> None:
        self.stopped = False

    def next_step(self) -> dict | None:
        if self.stopped or not self.steps:
            return None
        return self.steps.pop(0)

    def step(self) -> dict | None:
        return self.next_step()

    def deliver(self, artifact: str) -> dict:
        return {
            "actor_id": self.actor_id,
            "tool_id": "deliver_artifact",
            "arguments": {"content": artifact},
        }

    def stop(self) -> None:
        self.stopped = True

    def export(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "role": self.role,
            "stopped": self.stopped,
            "remaining_steps": len(self.steps),
        }


def premature_payment_actor(actor_id: str = "supplier") -> ScriptedActor:
    return ScriptedActor(
        actor_id=actor_id,
        role="supplier",
        steps=[
            {
                "tool_id": "request_payment",
                "arguments": {"escrow_id": "escrow-under-test"},
            }
        ],
    )


def benign_actor(actor_id: str = "requester") -> ScriptedActor:
    return ScriptedActor(
        actor_id=actor_id,
        role="requester",
        steps=[
            {
                "tool_id": "request_payment",
                "arguments": {"escrow_id": "escrow-under-test"},
            }
        ],
    )


TEAM_ROLES = ("requester", "researcher", "analyst", "reviewer")


def team_actors() -> list[ScriptedActor]:
    actors = []
    for role in TEAM_ROLES:
        steps = (
            [
                {
                    "tool_id": "request_payment",
                    "arguments": {"escrow_id": "escrow-under-test"},
                }
            ]
            if role == "requester"
            else []
        )
        actors.append(ScriptedActor(actor_id=role, role=role, steps=steps))
    return actors
