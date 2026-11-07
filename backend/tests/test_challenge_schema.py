"""The hand-written migration matches the models, and the state machines are closed."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa

from app.database import Base
from app.models import challenge, spend
from app.models.github import GitHubJob, JobKind, JobStatus

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "a7c3e9f1b2d4_challenge_engine_and_spend.py"
)
_NEW_TABLES = {
    "challenge_tasks",
    "challenge_contracts",
    "challenge_participants",
    "challenge_attempts",
    "challenge_claims",
    "challenge_artifacts",
    "challenge_verdicts",
    "findings",
    "account_budgets",
    "spend_reservations",
    "execution_usage",
}


class _RecordingOp:
    def __init__(self) -> None:
        self.tables: dict[str, list[sa.Column]] = {}
        self.indexes: dict[str, tuple[str, list[str], bool]] = {}
        self.added_columns: dict[str, list[sa.Column]] = {}

    def create_table(self, name, *items, **_kw):
        self.tables[name] = [item for item in items if isinstance(item, sa.Column)]

    def create_index(self, name, table, columns, unique=False, **_kw):
        self.indexes[name] = (table, list(columns), bool(unique))

    def add_column(self, table, column):
        self.added_columns.setdefault(table, []).append(column)


def _run_upgrade() -> _RecordingOp:
    spec = importlib.util.spec_from_file_location("mig_a7c3", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    recorder = _RecordingOp()
    module.op = recorder
    module.upgrade()
    return recorder


def _type_name(column: sa.Column) -> str:
    return type(column.type).__name__.upper()


def test_migration_creates_every_new_table_with_model_columns():
    recorder = _run_upgrade()
    assert set(recorder.tables) == _NEW_TABLES
    for name, columns in recorder.tables.items():
        model = Base.metadata.tables[name]
        migrated = {c.name: c for c in columns}
        assert set(migrated) == set(model.columns.keys()), name
        for col_name, col in migrated.items():
            model_col = model.columns[col_name]
            assert col.nullable == model_col.nullable, f"{name}.{col_name} nullable"
            assert _type_name(col) == _type_name(model_col), f"{name}.{col_name} type"


def test_migration_indexes_match_model_indexes():
    recorder = _run_upgrade()
    for name in _NEW_TABLES:
        for index in Base.metadata.tables[name].indexes:
            assert index.name in recorder.indexes, index.name
            table, cols, unique = recorder.indexes[index.name]
            assert table == name
            assert cols == [c.name for c in index.columns]
            assert unique == bool(index.unique)


def test_github_job_dedupe_key_column_and_partial_unique_index():
    recorder = _run_upgrade()
    added = {c.name: c for c in recorder.added_columns["github_jobs"]}
    assert added["dedupe_key"].nullable is True
    assert GitHubJob.__table__.columns["dedupe_key"].nullable is True
    table, cols, unique = recorder.indexes["uq_github_jobs_open_dedupe_key"]
    assert (table, cols, unique) == ("github_jobs", ["dedupe_key"], True)


def test_lease_idempotency_columns_and_index():
    from app.models.github import GitHubLease

    recorder = _run_upgrade()
    added = {c.name: c for c in recorder.added_columns["github_leases"]}
    for name in ("claim_idempotency_key", "submit_idempotency_key"):
        assert added[name].nullable is True
        assert GitHubLease.__table__.columns[name].nullable is True
    table, cols, unique = recorder.indexes["uq_github_leases_agent_claim_key"]
    assert (table, cols, unique) == (
        "github_leases",
        ["agent_id", "claim_idempotency_key"],
        True,
    )


def test_new_enum_values_fit_their_columns():
    assert JobStatus.CANCELLED.value == "cancelled"
    for kind in JobKind:
        assert len(kind.value) <= 32
    for state in challenge.ChallengeTaskState:
        assert len(state.value) <= 16
    for state in challenge.ClaimState:
        assert len(state.value) <= 24
    for verdict in challenge.VerdictKind:
        assert len(verdict.value) <= 24
    for status in challenge.FindingStatus:
        assert len(status.value) <= 16
    for cause in spend.UsageCause:
        assert len(cause.value) <= 32


def _assert_closed(transitions: dict[str, frozenset[str]], enum_cls) -> None:
    values = {member.value for member in enum_cls}
    assert set(transitions) == values
    for targets in transitions.values():
        assert targets <= values


def test_state_machines_cover_every_state():
    _assert_closed(challenge.TASK_TRANSITIONS, challenge.ChallengeTaskState)
    _assert_closed(challenge.CLAIM_TRANSITIONS, challenge.ClaimState)
    _assert_closed(spend.RESERVATION_TRANSITIONS, spend.ReservationStatus)
    for terminal in challenge.TERMINAL_TASK_STATES:
        assert challenge.TASK_TRANSITIONS[terminal] == frozenset()


def test_models_are_registered_on_metadata():
    namespace = SimpleNamespace(tables=Base.metadata.tables)
    assert _NEW_TABLES <= set(namespace.tables)
