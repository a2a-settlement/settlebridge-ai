"""Import CVE reach-ranking bundles produced by settlebridge-verifier.

Run from the backend directory::

    PYTHONPATH=. python -m app.cli.import_reach bundle.jsonl
    PYTHONPATH=. python -m app.cli.import_reach bundle.jsonl --replace-priorities

The database URL comes from application settings (``DATABASE_URL``). It is
never printed. Each invocation upserts the file in one transaction.
``imported_at`` is filled by the database. Omitted signal columns are stored
as NULL, never as 0. ``--replace-priorities`` deletes ``cve_priority`` rows
whose CVE is not in the bundle; ``package_reach`` rows are left in place.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func

from app.config import settings
from app.database import async_session
from app.models.reach import CvePriority, PackageReach

_URL_RE = re.compile(r"[a-z][a-z0-9+]*://\S+", re.IGNORECASE)
_BIGINT_MIN = -(2**63)
_BIGINT_MAX = 2**63 - 1
_TABLES = ("package_reach", "cve_priority")


class ReachImportError(Exception):
    """The bundle is not a valid reach-ranking JSONL file."""


class _Column:
    def __init__(self, name: str, kind: str, *, max_len: int | None = None, required: bool = False):
        self.name = name
        self.kind = kind
        self.max_len = max_len
        self.required = required


def _cols(*items: _Column) -> tuple[_Column, ...]:
    return items


_PACKAGE_COLUMNS = _cols(
    _Column("ecosystem", "str", max_len=32, required=True),
    _Column("name", "str", max_len=255, required=True),
    _Column("downloads_30d", "bigint"),
    _Column("downloads_total", "bigint"),
    _Column("dependents_direct", "bigint"),
    _Column("dependents_indirect", "bigint"),
    _Column("dependent_repos", "bigint"),
    _Column("docker_downloads", "bigint"),
    _Column("popcon_inst", "bigint"),
    _Column("exposed_instances", "bigint"),
    _Column("downloads_fetched_at", "ts"),
    _Column("dependents_fetched_at", "ts"),
    _Column("ecosystems_fetched_at", "ts"),
    _Column("popcon_fetched_at", "ts"),
    _Column("exposure_fetched_at", "ts"),
    _Column("errors", "errors"),
)
_CVE_COLUMNS = _cols(
    _Column("cve", "str", max_len=32, required=True),
    _Column("population", "str", max_len=16, required=True),
    _Column("confirmed_source", "str", max_len=64),
    _Column("ecosystem", "str", max_len=32),
    _Column("name", "str", max_len=255),
    _Column("downloads_pct", "float"),
    _Column("dependents_pct", "float"),
    _Column("docker_pct", "float"),
    _Column("reach_score", "float"),
    _Column("epss_percentile", "float"),
    _Column("threat_score", "float"),
    _Column("priority", "float"),
    _Column("kev", "bool"),
    _Column("tier", "str", max_len=8),
    _Column("weights_hash", "str", max_len=32, required=True),
    _Column("computed_at", "ts", required=True),
)
_SPECS = {
    "package_reach": (_PACKAGE_COLUMNS, PackageReach, ("ecosystem", "name")),
    "cve_priority": (_CVE_COLUMNS, CvePriority, ("cve",)),
}


def _fail(line_no: int, message: str) -> ReachImportError:
    return ReachImportError(f"line {line_no}: {message}")


def _string(value: object, column: _Column, line_no: int) -> str | None:
    if value is None:
        if column.required:
            raise _fail(line_no, f"missing required column {column.name!r}")
        return None
    if not isinstance(value, str):
        raise _fail(line_no, f"{column.name} must be a string or null")
    if column.required and value.strip() == "":
        raise _fail(line_no, f"missing required column {column.name!r}")
    if column.max_len is not None and len(value) > column.max_len:
        raise _fail(line_no, f"{column.name} is longer than {column.max_len} characters")
    return value


def _bigint(value: object, column: _Column, line_no: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(line_no, f"{column.name} must be an integer or null")
    if value < _BIGINT_MIN or value > _BIGINT_MAX:
        raise _fail(line_no, f"{column.name} is outside bigint range")
    return value


def _float(value: object, column: _Column, line_no: int) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(line_no, f"{column.name} must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise _fail(line_no, f"{column.name} must be a finite number or null")
    return number


def _bool(value: object, column: _Column, line_no: int) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _fail(line_no, f"{column.name} must be a boolean or null")
    return value


def _timestamp(value: object, column: _Column, line_no: int) -> datetime | None:
    if value is None:
        if column.required:
            raise _fail(line_no, f"missing required column {column.name!r}")
        return None
    if not isinstance(value, str) or value.strip() == "":
        raise _fail(line_no, f"{column.name} must be an ISO-8601 timestamp or null")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise _fail(line_no, f"{column.name} must be an ISO-8601 timestamp or null") from None
    if parsed.tzinfo is None:
        raise _fail(line_no, f"{column.name} must include a timezone")
    return parsed


def _errors(value: object, line_no: int) -> dict:
    if not isinstance(value, dict):
        raise _fail(line_no, "errors must be an object")
    return value


def _normalize(table: str, row: Mapping, line_no: int) -> dict:
    columns: tuple[_Column, ...] = _SPECS[table][0]
    allowed = {column.name for column in columns}
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise _fail(line_no, f"unknown column {unknown[0]!r} for table {table!r}")
    # Absent signal columns stay NULL. Never substitute 0.
    normalized: dict = {}
    for column in columns:
        if column.kind == "errors":
            if column.name not in row:
                normalized[column.name] = {}
                continue
            normalized[column.name] = _errors(row[column.name], line_no)
            continue
        present = column.name in row
        value = row.get(column.name)
        if column.kind == "str":
            if not present:
                value = None
            normalized[column.name] = _string(value, column, line_no)
        elif column.kind == "bigint":
            normalized[column.name] = _bigint(None if not present else value, column, line_no)
        elif column.kind == "float":
            normalized[column.name] = _float(None if not present else value, column, line_no)
        elif column.kind == "bool":
            normalized[column.name] = _bool(None if not present else value, column, line_no)
        elif column.kind == "ts":
            if not present:
                value = None
            normalized[column.name] = _timestamp(value, column, line_no)
        else:
            raise _fail(line_no, f"unsupported column {column.name!r}")
    return normalized


def _parse_line(line: str, line_no: int) -> tuple[str, dict]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise _fail(line_no, f"invalid JSON ({exc.msg})") from None
    if not isinstance(payload, dict):
        raise _fail(line_no, "expected an object")
    extra = sorted(set(payload) - {"table", "row"})
    if extra:
        raise _fail(line_no, f"unknown field {extra[0]!r}")
    table = payload.get("table")
    if table not in _TABLES:
        raise _fail(line_no, f"unknown table {table!r}")
    row = payload.get("row")
    if not isinstance(row, dict):
        raise _fail(line_no, "row must be an object")
    return table, _normalize(table, row, line_no)


def _load(path: Path) -> list[tuple[str, dict]]:
    if not path.is_file():
        raise ReachImportError(f"bundle not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ReachImportError("bundle must be UTF-8") from None
    except OSError as exc:
        raise ReachImportError(f"could not read bundle: {exc.strerror or 'read failed'}") from None
    parsed: list[tuple[str, dict]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "":
            continue
        parsed.append(_parse_line(line, line_no))
    return parsed


def _bind(db):
    get_bind = getattr(db, "get_bind", None)
    if callable(get_bind):
        return get_bind()
    sync = getattr(db, "sync", None)
    if sync is not None:
        return sync.get_bind()
    raise ReachImportError("database session is not bound")


def _dialect_insert(db):
    name = _bind(db).dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ReachImportError(f"unsupported database dialect {name}")
    return insert


async def _upsert(db, model, rows: list[dict], pk: tuple[str, ...]) -> None:
    if not rows:
        return
    insert = _dialect_insert(db)
    update_cols = [name for name in rows[0] if name not in pk]
    for row in rows:
        stmt = insert(model).values(**row)
        assignments = {name: stmt.excluded[name] for name in update_cols}
        assignments["imported_at"] = func.now()
        stmt = stmt.on_conflict_do_update(index_elements=list(pk), set_=assignments)
        await db.execute(stmt)


async def import_bundle(
    db, bundle: str | Path, *, replace_priorities: bool = False
) -> dict[str, int]:
    """Upsert one JSONL bundle. The caller commits this transaction."""
    parsed = _load(Path(bundle))
    package_rows = [row for table, row in parsed if table == "package_reach"]
    cve_rows = [row for table, row in parsed if table == "cve_priority"]
    await _upsert(db, PackageReach, package_rows, _SPECS["package_reach"][2])
    await _upsert(db, CvePriority, cve_rows, _SPECS["cve_priority"][2])
    removed = 0
    if replace_priorities:
        keep = list(dict.fromkeys(row["cve"] for row in cve_rows))
        if keep:
            stmt = delete(CvePriority).where(CvePriority.cve.notin_(keep))
        else:
            stmt = delete(CvePriority)
        result = await db.execute(stmt)
        removed = int(result.rowcount or 0)
    return {
        "package_reach": len(package_rows),
        "cve_priority": len(cve_rows),
        "priorities_removed": removed,
    }


def _redact(message: str) -> str:
    redacted = _URL_RE.sub("database://***", message)
    secret = settings.DATABASE_URL
    if secret:
        redacted = redacted.replace(secret, "database://***")
    return redacted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.import_reach",
        description="Import a CVE reach-ranking JSONL bundle.",
    )
    parser.add_argument("bundle", help="Path to a JSONL bundle from svf rank push.")
    parser.add_argument(
        "--replace-priorities",
        action="store_true",
        help="Delete cve_priority rows that are not in this bundle.",
    )
    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    try:
        async with async_session() as db:
            counts = await import_bundle(
                db, args.bundle, replace_priorities=args.replace_priorities
            )
            await db.commit()
    except ReachImportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(_redact(str(exc)), file=sys.stderr)
        return 1
    print(json.dumps(counts, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    raise SystemExit(main())
