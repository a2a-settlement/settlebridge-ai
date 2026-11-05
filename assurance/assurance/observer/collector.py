"""Independent observer. Actors cannot write its records."""

from __future__ import annotations

from dataclasses import dataclass, field


BLIND_SPOTS = (
    "private chain-of-thought is not collected",
    "channels other than the registered shared-resource tool are not observed",
)


@dataclass
class Observation:
    run_id: str
    kind: str
    actor_id: str
    detail: str
    authoritative: bool = True


@dataclass
class Collector:
    enabled: bool = True
    records: list[Observation] = field(default_factory=list)
    write_denials: list[str] = field(default_factory=list)

    def record(self, obs: Observation) -> None:
        if self.enabled:
            self.records.append(obs)

    def actor_write(self, actor_id: str) -> None:
        self.write_denials.append(actor_id)

    def coverage(self) -> dict:
        if not self.enabled:
            return {"complete": False, "gaps": ["collector disabled", *BLIND_SPOTS]}
        return {
            "complete": True,
            "gaps": list(BLIND_SPOTS),
            "events": len(self.records),
        }
