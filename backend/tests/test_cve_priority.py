"""CVE reach priority queue and JSONL bundle import. In-memory SQLite, no network."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.cli import import_reach
from app.cli.import_reach import ReachImportError, import_bundle
from app.config import settings
from app.models.fix_verifier import FixVerification, FixVerificationState
from app.models.reach import CvePriority, PackageReach
from app.services.cve_priority import priority_for, queued_by_priority
from tests.test_verifier_quota import _run, open_db as open_quota_db

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
HASH = "a" * 32
REPO = "acme/widget"


def _at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


@pytest.fixture()
def db():
    # SQLite rejects NULLS LAST inside CREATE INDEX. CreateTable skips indexes;
    # the migration still builds that index on PostgreSQL.
    eng, bridge = open_quota_db()
    with eng.begin() as conn:
        conn.execute(CreateTable(PackageReach.__table__))
        conn.execute(CreateTable(CvePriority.__table__))
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


def _sha(n: int) -> str:
    return f"{n:040d}"


def _queue(db, vuln_id: str, *, hours: int, state: str = FixVerificationState.QUEUED.value) -> None:
    db.sync.add(
        FixVerification(
            id=uuid.uuid4(),
            repo_full_name=REPO,
            vuln_id=vuln_id,
            base_sha="a" * 40,
            fix_sha=_sha(hours + 1),
            state=state,
            inputs={},
            result={},
            cost_cents=0,
            embargo_state="private",
            solver_kind="unknown",
            created_at=_at(hours),
        )
    )
    db.sync.commit()


def _priority(db, cve: str, priority: float | None) -> None:
    db.sync.add(
        CvePriority(
            cve=cve,
            population="pypi",
            weights_hash=HASH,
            computed_at=T0,
            priority=priority,
        )
    )
    db.sync.commit()


def _seed_queue(db) -> None:
    _queue(db, "CVE-MISS", hours=0)
    _queue(db, "CVE-LOW", hours=1)
    _queue(db, "CVE-NULL", hours=2)
    _queue(db, "CVE-HIGH", hours=3)
    _queue(db, "CVE-TIE", hours=4)
    _queue(db, "CVE-ZERO", hours=5)
    _queue(db, "CVE-DONE", hours=6, state=FixVerificationState.DONE.value)
    _priority(db, "CVE-LOW", 0.1)
    _priority(db, "CVE-NULL", None)
    _priority(db, "CVE-HIGH", 0.9)
    _priority(db, "CVE-TIE", 0.9)
    _priority(db, "CVE-ZERO", 0.0)
    _priority(db, "CVE-DONE", 1.0)


async def _capture(db, limit: int = 50):
    seen = []
    original = db.execute

    async def execute(statement, *args, **kwargs):
        seen.append(statement)
        return await original(statement, *args, **kwargs)

    db.execute = execute
    try:
        rows = await queued_by_priority(db, limit=limit)
    finally:
        db.execute = original
    return rows, seen


def _pg_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _ids(rows) -> list[str]:
    return [row.vuln_id for row in rows]


def test_flag_off_orders_queued_by_created_at(db, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_PRIORITY_QUEUE", False)
    _seed_queue(db)
    rows, seen = _run(_capture(db))
    assert _ids(rows) == ["CVE-MISS", "CVE-LOW", "CVE-NULL", "CVE-HIGH", "CVE-TIE", "CVE-ZERO"]
    sql = _pg_sql(seen[0])
    assert "cve_priority" not in sql
    assert "NULLS LAST" not in sql
    limited, _seen = _run(_capture(db, limit=1))
    assert _ids(limited) == ["CVE-MISS"]


def test_flag_on_orders_by_priority_with_nulls_last(db, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_PRIORITY_QUEUE", True)
    _seed_queue(db)
    rows, seen = _run(_capture(db))
    assert _ids(rows) == ["CVE-HIGH", "CVE-TIE", "CVE-LOW", "CVE-ZERO", "CVE-MISS", "CVE-NULL"]
    sql = _pg_sql(seen[0])
    assert "cve_priority" in sql
    assert "vuln_id" in sql
    assert "NULLS LAST" in sql
    assert "DESC" in sql
    limited, _seen = _run(_capture(db, limit=1))
    assert _ids(limited) == ["CVE-HIGH"]


def test_priority_for_is_gated_by_the_flag(db, monkeypatch):
    _priority(db, "CVE-HIGH", 0.9)
    _priority(db, "CVE-NULL", None)

    class Boom:
        async def execute(self, *args, **kwargs):
            raise AssertionError("database used while the priority queue is off")

    monkeypatch.setattr(settings, "FIX_VERIFIER_PRIORITY_QUEUE", False)
    assert _run(priority_for(Boom(), "CVE-HIGH")) is None
    assert _run(priority_for(db, "CVE-HIGH")) is None

    monkeypatch.setattr(settings, "FIX_VERIFIER_PRIORITY_QUEUE", True)
    found = _run(priority_for(db, "CVE-HIGH"))
    assert found is not None
    assert found.cve == "CVE-HIGH"
    assert found.priority == pytest.approx(0.9)
    assert _run(priority_for(db, "CVE-MISSING")) is None
    blank = _run(priority_for(db, "CVE-NULL"))
    assert blank is not None
    assert blank.priority is None


def _line(table: str, row: dict) -> str:
    return json.dumps({"table": table, "row": row}, separators=(",", ":"))


def _write(path, lines: list[str]):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _package(**overrides) -> str:
    row = {
        "ecosystem": "pypi",
        "name": "requests",
        "downloads_30d": 5,
        "downloads_total": 0,
        "errors": {"deps": "timeout"},
    }
    row.update(overrides)
    return _line("package_reach", row)


def _cve(cve: str = "CVE-2024-0001", **overrides) -> str:
    row = {
        "cve": cve,
        "population": "pypi",
        "ecosystem": "pypi",
        "name": "requests",
        "priority": 0.2,
        "kev": False,
        "confirmed_source": "osv",
        "weights_hash": HASH,
        "computed_at": "2026-11-17T00:00:00Z",
    }
    row.update(overrides)
    return _line("cve_priority", row)


def _count(db, model) -> int:
    db.sync.expire_all()
    return int(db.sync.scalar(select(func.count()).select_from(model)))


def _install_session(monkeypatch, db) -> None:
    class _Opened:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            if exc_type is not None:
                db.sync.rollback()
            return False

    monkeypatch.setattr(import_reach, "async_session", lambda: _Opened())


def test_import_twice_upserts_without_duplicating(db, tmp_path):
    path = _write(tmp_path / "first.jsonl", [_package(), _cve()])
    first = _run(import_bundle(db, path))
    _run(db.commit())
    second = _run(import_bundle(db, path))
    _run(db.commit())
    assert (
        first
        == second
        == {
            "package_reach": 1,
            "cve_priority": 1,
            "priorities_removed": 0,
        }
    )
    assert _count(db, PackageReach) == 1
    assert _count(db, CvePriority) == 1
    stored = db.sync.scalar(select(PackageReach))
    assert stored.downloads_30d == 5
    assert stored.downloads_total == 0
    assert stored.dependents_direct is None
    assert stored.errors == {"deps": "timeout"}

    package_row = {"ecosystem": "pypi", "name": "requests", "downloads_total": 9}
    cve_row = {
        "cve": "CVE-2024-0001",
        "population": "pypi",
        "priority": 0.8,
        "weights_hash": "b" * 32,
        "computed_at": "2026-11-17T01:00:00Z",
    }
    updated = _write(
        tmp_path / "second.jsonl",
        [_line("package_reach", package_row), _line("cve_priority", cve_row)],
    )
    third = _run(import_bundle(db, updated))
    _run(db.commit())
    assert third["package_reach"] == 1
    assert third["cve_priority"] == 1
    assert _count(db, PackageReach) == 1
    assert _count(db, CvePriority) == 1
    db.sync.expire_all()
    stored = db.sync.scalar(select(PackageReach))
    ranked = db.sync.scalar(select(CvePriority))
    assert stored.downloads_30d is None
    assert stored.downloads_total == 9
    assert stored.errors == {}
    assert stored.imported_at is not None
    assert ranked.priority == pytest.approx(0.8)
    assert ranked.kev is None
    assert ranked.confirmed_source is None
    assert ranked.ecosystem is None
    assert ranked.weights_hash == "b" * 32


def test_unknown_column_is_rejected(db, tmp_path):
    path = _write(
        tmp_path / "bad.jsonl",
        [
            _package(),
            _line(
                "cve_priority",
                {
                    "cve": "CVE-2024-0001",
                    "population": "pypi",
                    "weights_hash": HASH,
                    "computed_at": "2026-11-17T00:00:00Z",
                    "nope": 1,
                },
            ),
        ],
    )
    with pytest.raises(ReachImportError, match="unknown column 'nope' for table 'cve_priority'"):
        _run(import_bundle(db, path))
    assert _count(db, PackageReach) == 0
    assert _count(db, CvePriority) == 0


def test_replace_priorities_drops_cves_missing_from_the_bundle(db, monkeypatch, tmp_path, capsys):
    _install_session(monkeypatch, db)
    both = _write(
        tmp_path / "both.jsonl",
        [_package(), _cve("CVE-2024-0001"), _cve("CVE-2024-0002")],
    )
    only = _write(tmp_path / "only.jsonl", [_cve("CVE-2024-0001")])
    assert import_reach.main([str(both)]) == 0
    assert import_reach.main([str(only)]) == 0
    db.sync.expire_all()
    assert set(db.sync.scalars(select(CvePriority.cve)).all()) == {
        "CVE-2024-0001",
        "CVE-2024-0002",
    }
    assert set(db.sync.scalars(select(PackageReach.name)).all()) == {"requests"}
    capsys.readouterr()

    assert import_reach.main([str(only), "--replace-priorities"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    payload = json.loads(out)
    assert payload["priorities_removed"] == 1
    assert payload["cve_priority"] == 1
    assert "://" not in out
    assert "postgresql" not in out.lower()
    db.sync.expire_all()
    assert set(db.sync.scalars(select(CvePriority.cve)).all()) == {"CVE-2024-0001"}
    assert set(db.sync.scalars(select(PackageReach.name)).all()) == {"requests"}

    packages = _write(tmp_path / "packages.jsonl", [_package(name="urllib3")])
    assert import_reach.main([str(packages), "--replace-priorities"]) == 0
    db.sync.expire_all()
    assert set(db.sync.scalars(select(CvePriority.cve)).all()) == set()
    assert set(db.sync.scalars(select(PackageReach.name)).all()) == {"requests", "urllib3"}


def test_cli_rejects_unknown_column(db, monkeypatch, tmp_path, capsys):
    _install_session(monkeypatch, db)
    path = _write(
        tmp_path / "bad.jsonl",
        [_line("package_reach", {"ecosystem": "pypi", "name": "requests", "extra": 1})],
    )
    assert import_reach.main([str(path)]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "unknown column 'extra' for table 'package_reach'" in err
    assert "://" not in err
    assert _count(db, PackageReach) == 0


def test_upsert_sql_is_insert_on_conflict_do_update(tmp_path):
    class _Result:
        rowcount = 0

    class _Pg:
        def __init__(self):
            self.statements = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement, *args, **kwargs):
            self.statements.append(statement)
            return _Result()

    path = _write(
        tmp_path / "one.jsonl",
        [_line("package_reach", {"ecosystem": "pypi", "name": "requests"})],
    )
    fake = _Pg()
    counts = _run(import_bundle(fake, path))
    assert counts["package_reach"] == 1
    sql = _pg_sql(fake.statements[0])
    assert "ON CONFLICT (ecosystem, name) DO UPDATE" in sql
    insert_list, set_clause = sql.split("DO UPDATE SET", 1)
    assert "imported_at" not in insert_list.split("VALUES", 1)[0]
    assert "imported_at" in set_clause
    assert "downloads_30d" in sql
    compiled = fake.statements[0].compile(dialect=postgresql.dialect())
    assert compiled.params["downloads_30d"] is None


def test_cli_errors_redact_database_urls(monkeypatch, capsys, tmp_path):
    url = "postgresql+asyncpg://user:s3cret-test-value@localhost/db"
    monkeypatch.setattr(settings, "DATABASE_URL", url)

    class _Opened:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def commit(self):
            raise AssertionError("committed after a failed import")

    async def boom(_db, _bundle, *, replace_priorities=False):
        raise RuntimeError(f"connection to {url} failed")

    monkeypatch.setattr(import_reach, "async_session", lambda: _Opened())
    monkeypatch.setattr(import_reach, "import_bundle", boom)
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert import_reach.main([str(path)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "s3cret-test-value" not in err
    assert "postgresql" not in err.lower()
    assert "database://***" in err
