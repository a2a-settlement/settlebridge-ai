"""Verifier world for django-jsonfield-values-sqli (injection_execution).

The property: ``QuerySet.values()`` and ``values_list()`` on a JSONField never
put a caller-supplied key into SQL unquoted.

Parser-breakout convention: ``values_sql`` and ``values_list_sql`` compile that
selection in a forked child (stdlib SQLite, no server) and return the SQL
string. The parent parses it with ``sqlparse`` and records an injection
sentinel only when the key reaches an executable position: more than one
statement, an SQL comment, or an unquoted token taken from the key, instead of
one quoted column alias. ``filter_sql`` compiles a JSON key lookup whose key
is a bound parameter. A key that never enters the statement, or that stays
inside a quoted literal, does not fire. The test's assertions are not the
verdict.
"""

from __future__ import annotations

import sqlparse
from sqlparse.tokens import Comment, Keyword, Literal, Name

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_KEY_BYTES = 512
MAX_SQL_BYTES = 1024 * 1024
_COUNT_ALIAS = "row_count"
_MODES = frozenset({"values", "values_list", "filter"})
_SKELETON = frozenset(
    {
        "SELECT",
        "FROM",
        "WHERE",
        "AS",
        "COUNT",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
        "IN",
        "IS",
        "NOT",
        "NULL",
        "GROUP BY",
        "AND",
        "OR",
        "JSON_TYPE",
        "JSON_EXTRACT",
        "(",
        ")",
        ",",
        ".",
        "1",
    }
)
_COMMENT_MARKS = ("--", "/*", "*/")


def _require_key(key: object) -> str:
    checked = require_text(key, "key", MAX_KEY_BYTES)
    if not checked:
        raise ValueError("key must not be empty")
    if "__" in checked:
        raise ValueError("key must be a single JSON object key")
    return checked


def _configure() -> None:
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            INSTALLED_APPS=[],
            SECRET_KEY="sbc-django-jsonfield-values",
            USE_TZ=False,
        )
    django.setup()


def _compile_sql(mode: str, key: str) -> str:
    _configure()
    from django.db import models
    from django.db.models import Count

    class Record(models.Model):
        data = models.JSONField(null=True)

        class Meta:
            app_label = "sbc"
            managed = False
            db_table = "sbc_record"

    lookup = "data__" + key
    if mode == "values":
        query = Record.objects.values(lookup).annotate(row_count=Count("id"))
    elif mode == "values_list":
        query = Record.objects.values_list(lookup).annotate(row_count=Count("id"))
    elif mode == "filter":
        query = Record.objects.filter(**{lookup + "__isnull": False})
    else:
        raise ValueError("unknown query mode")
    sql, _params = query.query.get_compiler(using="default").as_sql()
    if type(sql) is not str:
        raise TypeError("compiled SQL is not a plain str")
    return sql


def _leaves(statement):
    for token in statement.tokens:
        if token.is_group:
            yield from _leaves(token)
        else:
            yield token


def _as_aliases(tokens: list) -> tuple[list[str], bool]:
    aliases: list[str] = []
    broken = False
    for index, token in enumerate(tokens):
        if token.ttype not in Keyword or token.value.upper() != "AS":
            continue
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if nxt is None or nxt.ttype is not Literal.String.Symbol:
            broken = True
        else:
            aliases.append(nxt.value)
    return aliases, broken


def _reached_executable(tokens: list, key: str) -> bool:
    """True when part of ``key`` is a comment or an unquoted token."""
    for token in tokens:
        if token.ttype in Comment:
            text = token.value
            if any(mark in text and mark in key for mark in _COMMENT_MARKS):
                return True
            if len(key) >= 2 and key in text:
                return True
            continue
        if token.ttype in Literal.String or token.ttype == Name.Placeholder:
            continue
        value = token.value
        if not value or value in _SKELETON:
            continue
        if value in key:
            return True
    return False


def _breakout_location(sql: str, key: str, *, expect_alias: bool) -> str | None:
    if key not in sql:
        return None
    try:
        statements = sqlparse.parse(sql)
    except Exception:
        return None
    if len(statements) != 1:
        return "django.JSONField: compiled statement is more than one statement"
    tokens = [token for token in _leaves(statements[0]) if not token.is_whitespace]
    if not _reached_executable(tokens, key) and not expect_alias:
        return None
    if expect_alias:
        expected = '"data__' + key + '"'
        allowed = {expected, '"' + _COUNT_ALIAS + '"'}
        aliases, broken = _as_aliases(tokens)
        quoted = (
            not broken
            and expected in aliases
            and all(alias in allowed for alias in aliases)
            and not any(token.ttype in Comment for token in tokens)
        )
        if quoted:
            return None
        if _reached_executable(tokens, key) or len(statements) != 1:
            return "django.JSONField: JSON key is not one quoted column alias"
        return None
    return "django.JSONField: key text is an unquoted SQL token"


class World(BaseWorld):
    def values_sql(self, key: str):
        """Compile ``values()`` SQL for one JSONField key, including its column alias.

        Returns the child outcome. ``outcome.broke_out`` is true only when the
        parsed statement puts ``key`` in an unquoted or executable position.
        """
        return self._selection("values", key)

    def values_list_sql(self, key: str):
        """Compile ``values_list()`` SQL for one JSONField key, including its column alias.

        Returns the child outcome. ``outcome.broke_out`` is true only when the
        parsed statement puts ``key`` in an unquoted or executable position.
        """
        return self._selection("values_list", key)

    def filter_sql(self, key: str):
        """Compile a JSON key ``isnull`` lookup. The key is a bound parameter.

        Returns the child outcome. ``outcome.broke_out`` is true only when the
        parsed statement puts ``key`` in an unquoted or executable position.
        """
        return self._selection("filter", key)

    def _selection(self, mode: str, key: str):
        if mode not in _MODES:
            raise ValueError("unknown query mode")
        checked = _require_key(key)
        outcome = self.call(_compile_sql, mode, checked)
        outcome.broke_out = False
        rendered = outcome.value
        if outcome.status != "ok" or type(rendered) is not str:
            return outcome
        if len(rendered.encode("utf-8", "surrogatepass")) > MAX_SQL_BYTES:
            return outcome
        location = _breakout_location(rendered, checked, expect_alias=mode != "filter")
        if location is None:
            return outcome
        outcome.broke_out = True
        capture.injection("command", location=location)
        return outcome
