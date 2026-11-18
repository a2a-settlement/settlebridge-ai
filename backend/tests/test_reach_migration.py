"""The reach-ranking migration matches the models and is the single Alembic head."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.database import Base
from app.models.reach import CvePriority, PackageReach

_REVISION = "e8b1c4d72a05"
_PARENT = "d2e5f7a9c1b3"
_BACKEND = Path(__file__).resolve().parents[1]
_MIGRATION = _BACKEND / "migrations" / "versions" / f"{_REVISION}_reach_ranking_tables.py"
_SIGNAL_NULLS = {
    "package_reach": {
        "downloads_30d",
        "downloads_total",
        "dependents_direct",
        "dependents_indirect",
        "dependent_repos",
        "docker_downloads",
        "popcon_inst",
        "exposed_instances",
        "downloads_fetched_at",
        "dependents_fetched_at",
        "ecosystems_fetched_at",
        "popcon_fetched_at",
        "exposure_fetched_at",
    },
    "cve_priority": {
        "confirmed_source",
        "ecosystem",
        "name",
        "downloads_pct",
        "dependents_pct",
        "docker_pct",
        "reach_score",
        "epss_percentile",
        "threat_score",
        "priority",
        "kev",
        "tier",
        "fix_status",
    },
}


class _RecordingOp:
    def __init__(self) -> None:
        self.tables: dict[str, list[sa.Column]] = {}
        self.indexes: dict[str, tuple[str, list, bool]] = {}
        self.dropped: list[str] = []

    def create_table(self, name, *items, **_kw):
        self.tables[name] = [item for item in items if isinstance(item, sa.Column)]

    def create_index(self, name, table, columns, unique=False, **_kw):
        self.indexes[name] = (table, list(columns), bool(unique))

    def drop_table(self, name):
        self.dropped.append(name)

    def get_bind(self):
        return None


def _load():
    spec = importlib.util.spec_from_file_location("reach_ranking_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run() -> tuple[object, _RecordingOp]:
    module = _load()
    recorder = _RecordingOp()
    module.op = recorder
    module.upgrade()
    return module, recorder


def _type_name(column: sa.Column) -> str:
    return type(column.type).__name__.upper()


def test_revision_is_the_only_head():
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert list(heads) == [_REVISION]
    head = script.get_revision(_REVISION)
    assert head is not None
    assert head.down_revision == _PARENT


def test_migration_matches_model_columns():
    module, recorder = _run()
    assert module.revision == _REVISION
    assert module.down_revision == _PARENT
    assert set(recorder.tables) == {"package_reach", "cve_priority"}
    for name, columns in recorder.tables.items():
        model = Base.metadata.tables[name]
        migrated = {column.name: column for column in columns}
        assert set(migrated) == set(model.columns.keys()), name
        for col_name, column in migrated.items():
            model_col = model.columns[col_name]
            assert column.nullable == model_col.nullable, f"{name}.{col_name} nullable"
            assert _type_name(column) == _type_name(model_col), f"{name}.{col_name} type"
            assert getattr(column.type, "length", None) == getattr(model_col.type, "length", None)
            if hasattr(column.type, "timezone"):
                assert column.type.timezone is True
                assert model_col.type.timezone is True
            assert (column.server_default is not None) == (model_col.server_default is not None)
        for signal in _SIGNAL_NULLS[name]:
            assert migrated[signal].nullable is True, signal
            assert migrated[signal].server_default is None, signal


def test_priority_index_is_desc_nulls_last():
    _module, recorder = _run()
    table, columns, unique = recorder.indexes["ix_cve_priority_priority"]
    assert (table, unique) == ("cve_priority", False)
    rendered = " ".join(getattr(column, "text", str(column)) for column in columns)
    assert rendered == "priority DESC NULLS LAST"
    index = next(iter(CvePriority.__table__.indexes))
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "CREATE INDEX ix_cve_priority_priority ON cve_priority (priority DESC NULLS LAST)" in sql
    assert PackageReach.__table__.indexes == set()


def test_downgrade_drops_both_tables():
    module, recorder = _run()
    module.downgrade()
    assert recorder.dropped == ["cve_priority", "package_reach"]


def test_models_are_exported():
    from app import models

    assert models.PackageReach is PackageReach
    assert models.CvePriority is CvePriority
    assert "PackageReach" in models.__all__
    assert "CvePriority" in models.__all__
