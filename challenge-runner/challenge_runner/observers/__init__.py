"""Verifier-owned observers (owner: P1-V). Frozen with the contract; the challenger
can neither see nor edit them.

Each observer reads captured runtime output and returns ObserverEvent values. The
authorization observer fires only when a seeded tenant-B canary token appears in a
response, log, or side channel available to the tenant-A identity.

The verdict rests on these events, never on the challenger's assertion. Observers
import only the standard library and ``challenge_runner.protocol`` (plus this
package's stdlib reader). A missing, oversized, or syntactically malformed capture
produces no event and never raises.

Capture files are UTF-8 JSON Lines in the capture directory. A symlink is not a
capture. One JSON line that is not an object drops that file. A well-formed object
that does not match the record below is skipped. At most 32 events are returned.

``canaries.jsonl`` (authorization)
    ``{"token": "<no whitespace, length >= 16>", "owner": "tenant-a"|"tenant-b"}``
    A token listed for both owners is ignored.

``authorization.jsonl`` (authorization)
    ``{"identity": "tenant-a"|"tenant-b", "channel": "response"|"log"|"side_channel",
    "body": "<text>", "location": "<where>"}``
    Fires ``canary_seen`` when a tenant-b-only token occurs in a tenant-a body on
    one of those channels. Tenant-A's own token does not fire. Underscores in the
    identity are treated as hyphens.

``integrity.jsonl`` (integrity)
    ``{"phase": "before"|"after", "identity": "<actor>", "authorized": <bool>,
    "table": "<name>", "row_id": "<id>", "protected": true, "digest": "<hash>"}``
    ``state`` (any JSON value) may replace ``digest``. Table and row id are single
    tokens. Fires ``state_changed`` when the before and after fingerprints of one
    protected row differ and the after record has ``authorized`` false. An
    authorized change, an unprotected row, or two snapshots of the same phase do
    not fire.

``injection.jsonl`` (injection)
    ``{"sentinel": "file"|"command", "triggered": true, "location": "<path>"}``
    Fires ``sentinel_triggered`` only when ``triggered`` is boolean true.

``resources.jsonl`` (denial of service)
    ``{"metric": "cpu_seconds"|"wall_seconds"|"memory_mb"|"disk_mb"|"max_processes",
    "value": <number>, "threshold": <number>}``
    ``threshold`` is the contract threshold copied by the verifier, not a sandbox
    cgroup limit. Fires ``threshold_exceeded`` when ``value > threshold``. Booleans
    and non-finite numbers do not count. One event per metric.

``regression.jsonl`` (regression)
    ``{"test_id": "<fixture test id>", "outcome": "passed"|"failed", "trusted": true}``
    Fires ``state_changed`` when a trusted fixture test's outcome in this run is
    ``failed``. Ids under ``challenge_tests/`` do not fire. Whether the test passed
    on base and failed on target is decided by comparing these per-run events.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from challenge_runner.observers.authorization_confidentiality import AuthorizationConfidentiality
from challenge_runner.observers.denial_of_service import DenialOfService
from challenge_runner.observers.injection_execution import InjectionExecution
from challenge_runner.observers.integrity import Integrity
from challenge_runner.observers.regression import Regression
from challenge_runner.protocol import ObserverEvent

OBSERVER_NAMES = (
    "authorization_confidentiality",
    "integrity",
    "injection_execution",
    "denial_of_service",
    "regression",
)


class Observer(Protocol):
    name: str

    def observe(self, capture_dir: str) -> Iterable[ObserverEvent]: ...


_REGISTRY: dict[str, Observer] = {
    observer.name: observer
    for observer in (
        AuthorizationConfidentiality(),
        Integrity(),
        InjectionExecution(),
        DenialOfService(),
        Regression(),
    )
}


def get_observer(name: str) -> Observer:
    if not isinstance(name, str):
        raise NotImplementedError(name)
    try:
        return _REGISTRY[name]
    except KeyError:
        raise NotImplementedError(name) from None
