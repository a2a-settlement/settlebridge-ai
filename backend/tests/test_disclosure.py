"""Embargo transitions, visibility, and fix-verifier opt-in. No database."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.challenge import EmbargoState, Finding
from app.models.github import GitHubRepoLink
from app.services.disclosure import (
    DEFAULT_EMBARGO,
    DisclosureError,
    can_view_details,
    transition,
    verifier_may_run,
)

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
EAST = timezone(timedelta(hours=2))
OWNER = "owner-user"
OPERATOR = "operator-user"
OTHER_OPERATOR = "other-operator"
STRANGER = "stranger-user"
ALLOWED = {
    (EmbargoState.PRIVATE, EmbargoState.NOTIFIED),
    (EmbargoState.NOTIFIED, EmbargoState.EMBARGOED),
    (EmbargoState.NOTIFIED, EmbargoState.DISCLOSED),
    (EmbargoState.EMBARGOED, EmbargoState.DISCLOSED),
}
_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "c8f1a2e4b6d0_fix_verifier_embargo_and_opt_in.py"
)


def _finding(state, until=None):
    return SimpleNamespace(embargo_state=state, embargo_until=until)


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_c8f1a2e4b6d0", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("source", list(EmbargoState))
@pytest.mark.parametrize("target", list(EmbargoState))
def test_every_transition_is_allowed_or_rejected(source, target):
    original_until = NOW + timedelta(days=3)
    finding = _finding(source, original_until)
    explicit_until = NOW + timedelta(days=40)
    if (source, target) in ALLOWED:
        kwargs = {"embargo_until": explicit_until} if target is EmbargoState.EMBARGOED else {}
        transition(finding, target, now=NOW, **kwargs)
        assert finding.embargo_state == target
        if target is EmbargoState.EMBARGOED:
            assert finding.embargo_until == explicit_until
        else:
            assert finding.embargo_until == original_until
        return
    with pytest.raises(DisclosureError):
        transition(finding, target, now=NOW, embargo_until=explicit_until)
    assert finding.embargo_state == source
    assert finding.embargo_until == original_until


def test_string_states_follow_the_same_rules():
    finding = _finding("private")
    transition(finding, "notified", now=NOW)
    assert finding.embargo_state == EmbargoState.NOTIFIED
    transition(finding, "disclosed", now=NOW)
    assert finding.embargo_state == EmbargoState.DISCLOSED


def test_unknown_state_is_rejected_without_mutation():
    finding = _finding("private", NOW + timedelta(days=1))
    with pytest.raises(DisclosureError):
        transition(finding, "public", now=NOW)
    assert finding.embargo_state == "private"
    assert finding.embargo_until == NOW + timedelta(days=1)


def test_default_embargo_is_ninety_days_from_now():
    finding = _finding("notified", NOW + timedelta(days=1))
    transition(finding, EmbargoState.EMBARGOED, now=NOW)
    assert finding.embargo_state == EmbargoState.EMBARGOED
    assert finding.embargo_until == NOW + DEFAULT_EMBARGO
    assert DEFAULT_EMBARGO == timedelta(days=90)


def test_embargo_until_must_be_strictly_in_the_future():
    finding = _finding("notified")
    for deadline in (NOW, NOW - timedelta(seconds=1)):
        with pytest.raises(DisclosureError):
            transition(finding, "embargoed", now=NOW, embargo_until=deadline)
        assert finding.embargo_state == "notified"
        assert finding.embargo_until is None


def test_naive_clock_is_rejected():
    finding = _finding("notified")
    naive_now = datetime(2026, 11, 10, 18, 0)
    with pytest.raises(DisclosureError):
        transition(finding, "embargoed", now=naive_now)
    with pytest.raises(DisclosureError):
        transition(
            finding,
            "embargoed",
            now=NOW,
            embargo_until=naive_now + timedelta(days=1),
        )
    private = _finding("private")
    with pytest.raises(DisclosureError):
        transition(private, "notified", now=naive_now)
    assert finding.embargo_state == "notified"
    assert private.embargo_state == "private"


def test_embargo_until_compares_absolute_instants():
    finding = _finding("notified")
    # 19:00 at UTC+2 is 17:00 UTC, before NOW.
    earlier = datetime(2026, 11, 10, 19, 0, tzinfo=EAST)
    with pytest.raises(DisclosureError):
        transition(finding, "embargoed", now=NOW, embargo_until=earlier)
    assert finding.embargo_state == "notified"
    later = datetime(2026, 11, 10, 21, 0, tzinfo=EAST)
    transition(finding, "embargoed", now=NOW, embargo_until=later)
    assert finding.embargo_until == later


def test_disclosed_keeps_the_existing_embargo_date():
    deadline = NOW + timedelta(days=12)
    finding = _finding(EmbargoState.EMBARGOED, deadline)
    transition(finding, EmbargoState.DISCLOSED, now=NOW)
    assert finding.embargo_state == EmbargoState.DISCLOSED
    assert finding.embargo_until == deadline


@pytest.mark.parametrize("state", list(EmbargoState))
@pytest.mark.parametrize(
    ("viewer", "visible_before_disclosure"),
    [
        (OWNER, True),
        (OPERATOR, True),
        (OTHER_OPERATOR, True),
        (STRANGER, False),
        (None, False),
    ],
)
def test_can_view_details_matrix(state, viewer, visible_before_disclosure):
    finding = _finding(state.value)
    visible = can_view_details(
        finding,
        viewer_user_id=viewer,
        owner_user_id=OWNER,
        operator_ids=[OPERATOR, OTHER_OPERATOR],
    )
    if state is EmbargoState.DISCLOSED:
        assert visible is True
    else:
        assert visible is visible_before_disclosure


def test_anonymous_viewer_is_not_treated_as_a_missing_owner():
    finding = _finding(EmbargoState.PRIVATE)
    assert (
        can_view_details(
            finding,
            viewer_user_id=None,
            owner_user_id=None,
            operator_ids=set(),
        )
        is False
    )


def test_disclosed_string_state_is_visible_to_a_stranger():
    finding = _finding("disclosed")
    assert (
        can_view_details(
            finding,
            viewer_user_id=STRANGER,
            owner_user_id=OWNER,
            operator_ids=set(),
        )
        is True
    )


@pytest.mark.parametrize(
    ("enabled", "opted_in", "expected"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
        (None, True, False),
        (True, None, False),
    ],
)
def test_verifier_may_run(enabled, opted_in, expected):
    link = SimpleNamespace(enabled=enabled, fix_verifier_opt_in=opted_in)
    assert verifier_may_run(link) is expected


def test_model_columns_match_the_embargo_contract():
    state = Finding.__table__.columns["embargo_state"]
    until = Finding.__table__.columns["embargo_until"]
    opt_in = GitHubRepoLink.__table__.columns["fix_verifier_opt_in"]
    assert state.nullable is False
    assert set(state.type.enums) == {"private", "notified", "embargoed", "disclosed"}
    assert "private" in str(state.server_default.arg)
    assert until.nullable is True
    assert until.type.timezone is True
    assert opt_in.nullable is False
    assert "false" in str(opt_in.server_default.arg).lower()


def test_migration_revision_and_enum_round_trip():
    module = _load_migration()
    assert module.revision == "c8f1a2e4b6d0"
    assert module.down_revision == "a7c3e9f1b2d4"
    assert list(module.embargostate.enums) == [
        "private",
        "notified",
        "embargoed",
        "disclosed",
    ]

    calls: list[tuple] = []

    def create(bind, checkfirst=False):
        calls.append(("create_type", checkfirst, bind))

    def drop(bind, checkfirst=False):
        calls.append(("drop_type", checkfirst))

    module.embargostate.create = create
    module.embargostate.drop = drop

    class _Op:
        def get_bind(self):
            return "bind"

        def add_column(self, table, column):
            calls.append(("add", table, column.name, column.nullable, type(column.type).__name__))

        def drop_column(self, table, name):
            calls.append(("drop", table, name))

    module.op = _Op()
    module.upgrade()
    module.downgrade()

    assert calls[0] == ("create_type", True, "bind")
    added = [item for item in calls if item[0] == "add"]
    assert added == [
        ("add", "findings", "embargo_state", False, "ENUM"),
        ("add", "findings", "embargo_until", True, "DateTime"),
        ("add", "github_repo_links", "fix_verifier_opt_in", False, "Boolean"),
    ]
    assert ("drop", "github_repo_links", "fix_verifier_opt_in") in calls
    assert ("drop", "findings", "embargo_until") in calls
    assert ("drop", "findings", "embargo_state") in calls
    drop_type_at = calls.index(("drop_type", True))
    embargo_column_drop = calls.index(("drop", "findings", "embargo_state"))
    assert embargo_column_drop < drop_type_at
