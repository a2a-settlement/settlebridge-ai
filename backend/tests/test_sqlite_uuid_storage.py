"""UUID columns on the SQLite test engines keep every id as text.

See tests/conftest.py. Without the type override there, SQLite stores an id
whose hex looks like a number as an integer or a real, and the suite fails at
random.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.models.github import GitHubInstallation
from app.models.user import User

# Valid version 4 ids that SQLite reads as numbers when the column has NUMERIC affinity.
NUMBER_LIKE_IDS = [
    # All decimal digits: stored as a real (too big for an integer).
    uuid.UUID("96417095-0793-4755-8234-123456789012"),
    # Digits around one "e": stored as a real in exponent form.
    uuid.UUID("12345678-1234-4234-8234-123456789e12"),
    # Leading zeros and a small value: stored as an integer.
    uuid.UUID("00000000-0000-4000-8000-000000000001"),
]


def _table() -> Table:
    return Table(
        "things",
        MetaData(),
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("owner_id", UUID(as_uuid=True), nullable=True),
    )


@pytest.mark.parametrize("value", NUMBER_LIKE_IDS, ids=str)
def test_number_like_uuid_round_trips_on_sqlite(value):
    engine = create_engine("sqlite://")
    things = _table()
    things.create(engine)
    with engine.begin() as conn:
        conn.execute(insert(things).values(id=value, owner_id=value))
        assert conn.execute(select(things.c.id, things.c.owner_id)).one() == (value, value)
        assert conn.execute(select(things.c.id).where(things.c.id == value)).scalar_one() == value
        stored = conn.execute(text("SELECT typeof(id), typeof(owner_id) FROM things")).one()
    assert tuple(stored) == ("text", "text")


@pytest.mark.parametrize("value", NUMBER_LIKE_IDS, ids=str)
def test_number_like_uuid_survives_an_orm_flush_of_a_model(value):
    # The ORM insert asks SQLite to return the id; that is where the float surfaced.
    engine = create_engine("sqlite://")
    GitHubInstallation.__table__.create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                GitHubInstallation(id=value, installation_id=1, account_login="acme"),
                GitHubInstallation(id=uuid.uuid4(), installation_id=2, account_login="other"),
            ]
        )
        session.commit()
        assert session.get(GitHubInstallation, value).account_login == "acme"


def test_model_uuid_columns_are_char_on_sqlite_and_native_on_postgres():
    engine = create_engine("sqlite://")
    sqlite_ddl = str(CreateTable(User.__table__).compile(engine))
    assert "id CHAR(32) NOT NULL" in sqlite_ddl
    assert "UUID" not in sqlite_ddl
    postgres_ddl = str(CreateTable(User.__table__).compile(dialect=postgresql.dialect()))
    assert "id UUID NOT NULL" in postgres_ddl
