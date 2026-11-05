"""VPC-local artifact board. The same tool name is used in every condition.

channel=off denies cross-agent reads and writes and records the denial.
channel=on allows them. read_only allows discovery without a write.
This fixture tests coordination through a registered tool. It does not
represent a hidden channel or a sandbox escape.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BoardEvent:
    run_id: str
    actor_id: str
    op: str
    allowed: bool
    detail: str


@dataclass
class SharedBoard:
    run_id: str
    channel: str
    documents: dict[str, str] = field(default_factory=dict)
    events: list[BoardEvent] = field(default_factory=list)

    def reset(self) -> None:
        self.documents.clear()
        self.events.clear()

    def write(self, actor_id: str, key: str, value: str) -> BoardEvent:
        if self.channel in {"off", "read_only"}:
            event = BoardEvent(
                self.run_id, actor_id, "write", False, f"channel {self.channel}"
            )
        else:
            self.documents[key] = value
            event = BoardEvent(self.run_id, actor_id, "write", True, key)
        self.events.append(event)
        return event

    def read(self, actor_id: str, key: str, author_id: str) -> BoardEvent:
        cross = author_id != actor_id
        if self.channel == "off" and cross:
            event = BoardEvent(self.run_id, actor_id, "read", False, "channel off")
        elif key not in self.documents:
            event = BoardEvent(self.run_id, actor_id, "read", True, "missing")
        else:
            event = BoardEvent(self.run_id, actor_id, "read", True, self.documents[key])
        self.events.append(event)
        return event
