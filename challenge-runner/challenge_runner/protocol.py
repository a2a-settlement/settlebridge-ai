"""challenge-runner/v1 job and result shapes (S0, coordinator-owned).

Stdlib only. Field names and meanings match backend/app/schemas/challenge.py
(RunnerJob, RunnerResult, ObserverEvent, ResourceLimits) and the generated
docs/cyber/runner-protocol.schema.json. Unknown keys are rejected.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any

PROTOCOL = "challenge-runner/v1"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
STAGES = ("dependencies", "execute")
RESULT_STATUSES = ("ok", "test_failed", "limit_exceeded", "sandbox_error", "cancelled")
EVENT_KINDS = ("canary_seen", "state_changed", "sentinel_triggered", "threshold_exceeded")
DEFAULT_ALLOWLIST = ("pypi.org", "files.pythonhosted.org")


class ProtocolError(ValueError):
    pass


def _check_keys(cls, data: dict[str, Any]) -> None:
    allowed = {f.name for f in fields(cls)}
    extra = set(data) - allowed
    if extra:
        raise ProtocolError(f"{cls.__name__}: unknown keys {sorted(extra)}")


def _digest(value: Any, name: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not DIGEST_RE.match(value):
        raise ProtocolError(f"{name} must be sha256:<64 hex>")
    return value


@dataclass(frozen=True)
class ResourceLimits:
    cpu_seconds: int
    memory_mb: int
    wall_seconds: int
    disk_mb: int
    max_processes: int = 256

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourceLimits":
        _check_keys(cls, data)
        bounds = {
            "cpu_seconds": 3600,
            "memory_mb": 8192,
            "wall_seconds": 3600,
            "disk_mb": 20480,
            "max_processes": 4096,
        }
        values = {}
        for name, cap in bounds.items():
            raw = data.get(name, 256 if name == "max_processes" else None)
            if not isinstance(raw, int) or isinstance(raw, bool) or not 0 < raw <= cap:
                raise ProtocolError(f"limits.{name} must be an int in 1..{cap}")
            values[name] = raw
        return cls(**values)


@dataclass(frozen=True)
class RunnerJob:
    execution_id: str
    stage: str
    task_id: str
    source_tarball: str
    limits: ResourceLimits
    protocol: str = PROTOCOL
    wheelhouse_manifest: str | None = None
    fixtures_digest: str | None = None
    challenger_bundle: str | None = None
    python_version: str = "3.11"
    install_command: tuple[str, ...] = ()
    test_command: tuple[str, ...] = ()
    allowlist_hosts: tuple[str, ...] = DEFAULT_ALLOWLIST
    observers: tuple[str, ...] = ()
    canaries_digest: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunnerJob":
        _check_keys(cls, data)
        if data.get("protocol", PROTOCOL) != PROTOCOL:
            raise ProtocolError("unsupported protocol")
        if data.get("stage") not in STAGES:
            raise ProtocolError("stage must be dependencies or execute")
        execution_id = data.get("execution_id")
        if not isinstance(execution_id, str) or not 8 <= len(execution_id) <= 128:
            raise ProtocolError("execution_id must be 8..128 chars")
        return cls(
            protocol=PROTOCOL,
            execution_id=execution_id,
            stage=data["stage"],
            task_id=str(data["task_id"]),
            source_tarball=_digest(data.get("source_tarball"), "source_tarball"),
            limits=ResourceLimits.from_dict(data.get("limits") or {}),
            wheelhouse_manifest=_digest(
                data.get("wheelhouse_manifest"), "wheelhouse_manifest", optional=True
            ),
            fixtures_digest=_digest(data.get("fixtures_digest"), "fixtures_digest", True),
            challenger_bundle=_digest(data.get("challenger_bundle"), "challenger_bundle", True),
            python_version=str(data.get("python_version", "3.11")),
            install_command=tuple(data.get("install_command") or ()),
            test_command=tuple(data.get("test_command") or ()),
            allowlist_hosts=tuple(data.get("allowlist_hosts") or DEFAULT_ALLOWLIST),
            observers=tuple(data.get("observers") or ()),
            canaries_digest=_digest(data.get("canaries_digest"), "canaries_digest", True),
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("install_command", "test_command", "allowlist_hosts", "observers"):
            out[key] = list(out[key])
        return out


@dataclass(frozen=True)
class ObserverEvent:
    observer: str
    kind: str
    detail: str
    location: str

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ProtocolError(f"observer event kind {self.kind!r}")


@dataclass(frozen=True)
class RunnerResult:
    execution_id: str
    stage: str
    status: str
    image_digest: str
    source_digest: str
    protocol: str = PROTOCOL
    exit_code: int | None = None
    cpu_seconds: int = 0
    wall_seconds: int = 0
    wheelhouse_manifest: str | None = None
    observer_events: tuple[ObserverEvent, ...] = field(default_factory=tuple)
    log_digest: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ProtocolError("stage")
        if self.status not in RESULT_STATUSES:
            raise ProtocolError("status")
        _digest(self.image_digest, "image_digest")
        _digest(self.source_digest, "source_digest")
        _digest(self.wheelhouse_manifest, "wheelhouse_manifest", optional=True)
        _digest(self.log_digest, "log_digest", optional=True)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["observer_events"] = [asdict(e) for e in self.observer_events]
        return out
