"""Embargo transitions for findings in someone else's code.

A confirmed incomplete fix is an undisclosed vulnerability. The finding stays
private until the maintainer is notified. Details stay with the repository owner
and SettleBridge operators until the finding is disclosed. The fix verifier runs
only on a repository link that is enabled and whose owner opted in.

Allowed moves are private to notified, notified to embargoed, embargoed to
disclosed, and notified to disclosed when the maintainer agrees to publish.
No move goes backward. Private does not jump to disclosed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.challenge import EmbargoState

DEFAULT_EMBARGO_DAYS = 90
DEFAULT_EMBARGO = timedelta(days=DEFAULT_EMBARGO_DAYS)

# from-state -> allowed to-states. Every other move is rejected.
EMBARGO_TRANSITIONS: dict[str, frozenset[str]] = {
    EmbargoState.PRIVATE.value: frozenset({EmbargoState.NOTIFIED.value}),
    EmbargoState.NOTIFIED.value: frozenset(
        {EmbargoState.EMBARGOED.value, EmbargoState.DISCLOSED.value}
    ),
    EmbargoState.EMBARGOED.value: frozenset({EmbargoState.DISCLOSED.value}),
    EmbargoState.DISCLOSED.value: frozenset(),
}


class DisclosureError(ValueError):
    """The embargo transition or the embargo date was rejected."""


def _as_state(value: object) -> str:
    if isinstance(value, EmbargoState):
        return value.value
    if isinstance(value, str):
        try:
            return EmbargoState(value).value
        except ValueError:
            raise DisclosureError(f"unknown embargo state {value!r}") from None
    raise DisclosureError(f"unknown embargo state {value!r}")


def _aware(value: object, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.tzinfo.utcoffset(value) is None
    ):
        raise DisclosureError(f"{label} must be a timezone-aware datetime")
    return value


def transition(finding, to_state, *, now, embargo_until=None):
    """Move ``finding`` to ``to_state`` when that move is allowed.

    ``now`` is the clock for this call. Entering ``embargoed`` requires
    ``embargo_until`` to be after ``now``. When ``embargo_until`` is omitted, the
    embargo ends 90 days after ``now`` (the notification clock the caller passes).
    A rejected call leaves the finding unchanged.
    """
    current = _as_state(finding.embargo_state)
    target = _as_state(to_state)
    moment = _aware(now, "now")
    if target not in EMBARGO_TRANSITIONS.get(current, frozenset()):
        raise DisclosureError(f"cannot transition embargo from {current} to {target}")
    deadline = None
    if target == EmbargoState.EMBARGOED.value:
        if embargo_until is None:
            deadline = moment + DEFAULT_EMBARGO
        else:
            deadline = _aware(embargo_until, "embargo_until")
            if deadline <= moment:
                raise DisclosureError("embargo_until must be in the future")
    finding.embargo_state = EmbargoState(target)
    if deadline is not None:
        finding.embargo_until = deadline
    return finding


def can_view_details(finding, *, viewer_user_id, owner_user_id, operator_ids) -> bool:
    """Finding details are for the task owner and operators until disclosure.

    An anonymous viewer is not the owner, even when ``owner_user_id`` is missing.
    """
    if viewer_user_id is not None and viewer_user_id == owner_user_id:
        return True
    if viewer_user_id is not None and viewer_user_id in operator_ids:
        return True
    return _as_state(finding.embargo_state) == EmbargoState.DISCLOSED.value


def verifier_may_run(link) -> bool:
    """True only when the repository link is enabled and the owner opted in."""
    return bool(link.enabled) and bool(link.fix_verifier_opt_in)
