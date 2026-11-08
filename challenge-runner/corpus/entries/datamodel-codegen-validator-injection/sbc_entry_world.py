"""Verifier world for datamodel-codegen-validator-injection (injection_execution).

The property: code generated from extra template data never contains a Python
expression taken from that data.

``load_schema`` generates one module from a JSON Schema. ``generate`` generates
that same schema again with extra template data. Both library calls run in a
forked child that imports the library fresh. The parent parses the module with
the stdlib ``ast`` and records an injection sentinel only when a string value
from the extra data reaches an executable or import-path position: a call
argument or callee that is not a string literal, or an import that the schema
alone did not emit. Text that stays inside a string literal or a comment does
not fire. The test's assertions are not the verdict.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_SCHEMA_BYTES = 64 * 1024
MAX_TEMPLATE_BYTES = 64 * 1024
MAX_STRING_BYTES = 8 * 1024
MAX_KEY_BYTES = 256
MAX_SOURCE_BYTES = 200 * 1024
MAX_DEPTH = 8
MAX_ITEMS = 32
MAX_STRINGS = 64
_IDENT = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


def _reject_number(value: int | float, name: str) -> None:
    if type(value) is float and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError(f"{name} must be a finite number")
    if abs(value) > 10**12:
        raise ValueError(f"{name} is out of range")


def _copy_json(value: object, name: str, depth: int) -> object:
    if depth > MAX_DEPTH:
        raise ValueError(f"{name} is nested too deeply")
    if type(value) is str:
        return require_text(value, name, MAX_STRING_BYTES)
    if value is None or type(value) is bool:
        return value
    if type(value) is int or type(value) is float:
        _reject_number(value, name)
        return value
    if type(value) is list:
        if len(value) > MAX_ITEMS:
            raise ValueError(f"{name} has more than {MAX_ITEMS} items")
        return [_copy_json(item, name, depth + 1) for item in value]
    if type(value) is dict:
        if len(value) > MAX_ITEMS:
            raise ValueError(f"{name} has more than {MAX_ITEMS} keys")
        copied: dict[str, object] = {}
        for key, item in value.items():
            copied[require_text(key, f"{name} key", MAX_KEY_BYTES)] = _copy_json(
                item, name, depth + 1
            )
        return copied
    raise TypeError(f"{name} must be a JSON value")


def _require_template(template_data: object) -> dict:
    if type(template_data) is not dict:
        raise TypeError("template_data must be a plain dict")
    copied = _copy_json(template_data, "template_data", 0)
    if type(copied) is not dict:
        raise TypeError("template_data must be a plain dict")
    for key, item in copied.items():
        if type(item) is not dict:
            raise TypeError(f"template_data[{key!r}] must be a dict")
    raw = json.dumps(copied).encode("utf-8")
    if len(raw) > MAX_TEMPLATE_BYTES:
        raise ValueError(f"template_data is longer than {MAX_TEMPLATE_BYTES} bytes")
    return copied


def _string_leaves(value: object, found: list[str]) -> None:
    if len(found) > MAX_STRINGS:
        return
    if type(value) is str:
        if value.strip():
            found.append(value)
        return
    if type(value) is dict:
        for item in value.values():
            _string_leaves(item, found)
        return
    if type(value) is list:
        for item in value:
            _string_leaves(item, found)


def _generate(schema: str, template_data: dict | None) -> str:
    from datamodel_code_generator import DataModelType, InputFileType, generate

    options: dict = {
        "input_file_type": InputFileType.JsonSchema,
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "formatters": [],
        "disable_timestamp": True,
    }
    if template_data is not None:
        options["extra_template_data"] = template_data
    rendered = generate(schema, **options)
    if type(rendered) is not str:
        raise TypeError("generate did not return a module string")
    return rendered


def _parsed(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(source):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _offset(starts: list[int], lineno: int, column: int) -> int:
    if lineno < 1 or lineno > len(starts):
        return 0
    return starts[lineno - 1] + column


def _span(starts: list[int], node: ast.AST) -> tuple[int, int] | None:
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None)
    col = getattr(node, "col_offset", None)
    end_col = getattr(node, "end_col_offset", None)
    if None in (lineno, end_lineno, col, end_col):
        return None
    return _offset(starts, lineno, col), _offset(starts, end_lineno, end_col)


def _comment_spans(source: str) -> list[tuple[int, int]] | None:
    starts = _line_starts(source)
    spans: list[tuple[int, int]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            spans.append(
                (
                    _offset(starts, token.start[0], token.start[1]),
                    _offset(starts, token.end[0], token.end[1]),
                )
            )
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return spans


def _string_spans(source: str, tree: ast.AST) -> list[tuple[int, int]]:
    starts = _line_starts(source)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and type(node.value) is str:
            span = _span(starts, node)
            if span is not None:
                spans.append(span)
    return spans


def _inside(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(span_start <= start and end <= span_end for span_start, span_end in spans)


def _identish(value: str) -> bool:
    parts = value.split(".")
    return len(parts) >= 1 and all(_IDENT.fullmatch(part) for part in parts) and ".." not in value


def _bounded(source: str, start: int, end: int) -> bool:
    before = source[start - 1] if start else ""
    after = source[end] if end < len(source) else ""
    if before and (before.isalnum() or before == "_"):
        return False
    if after and (after.isalnum() or after == "_"):
        return False
    return True


def _find(source: str, needle: str, *, bounds: bool) -> list[tuple[int, int]]:
    if not needle:
        return []
    found: list[tuple[int, int]] = []
    start = 0
    while len(found) < 64:
        index = source.find(needle, start)
        if index < 0:
            break
        end = index + len(needle)
        if not bounds or _bounded(source, index, end):
            found.append((index, end))
        start = index + 1
    return found


def _dotted(value: str) -> tuple[str, str] | None:
    parts = value.split(".")
    if len(parts) < 2 or not all(_IDENT.fullmatch(part) for part in parts):
        return None
    return ".".join(parts[:-1]), parts[-1]


def _imports(tree: ast.AST) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                found.add((module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "." in alias.name:
                    module, _, name = alias.name.rpartition(".")
                    found.add((module, name))
                else:
                    found.add(("", alias.name))
    return found


def _called(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == name:
            return True
    return False


def _call_spans(source: str, tree: ast.AST) -> list[tuple[int, int]]:
    starts = _line_starts(source)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            span = _span(starts, node)
            if span is not None:
                spans.append(span)
    return spans


def _in_call(source: str, tree: ast.AST, needles: list[str], safe: list[tuple[int, int]]) -> bool:
    calls = _call_spans(source, tree)
    for needle in needles:
        for start, end in _find(source, needle, bounds=_identish(needle)):
            if _inside(safe, start, end):
                continue
            if _inside(calls, start, end):
                return True
    return False


def _only_quoted(source: str, needle: str, safe: list[tuple[int, int]]) -> bool:
    spans = _find(source, needle, bounds=_identish(needle))
    if not spans:
        return False
    return all(_inside(safe, start, end) for start, end in spans)


def _import_name(
    source: str,
    tree: ast.AST,
    baseline: set[tuple[str, str]],
    needles: list[str],
    safe: list[tuple[int, int]],
) -> str | None:
    fresh = _imports(tree) - baseline
    for needle in needles:
        pair = _dotted(needle)
        if pair is None or pair not in fresh or _only_quoted(source, needle, safe):
            continue
        return pair[1]
    return None


def _token_break(source: str, needles: list[str]) -> bool:
    comments = _comment_spans(source)
    if comments is None:
        return False
    starts = _line_starts(source)
    strings: list[tuple[int, int]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.STRING:
                continue
            strings.append(
                (
                    _offset(starts, token.start[0], token.start[1]),
                    _offset(starts, token.end[0], token.end[1]),
                )
            )
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return False
    safe = strings + comments
    for needle in needles:
        for start, end in _find(source, needle, bounds=_identish(needle)):
            if not _inside(safe, start, end):
                return True
    return False


def _location(source: str, baseline: str, needles: list[str]) -> tuple[str, str] | None:
    if not needles:
        return None
    tree = _parsed(source)
    if tree is None:
        if _token_break(source, needles):
            return ("command", "datamodel_code_generator: extra template data reached a call")
        return None
    safe = _string_spans(source, tree)
    comments = _comment_spans(source)
    if comments:
        safe.extend(comments)
    if _in_call(source, tree, needles, safe):
        return ("command", "datamodel_code_generator: extra template data reached a call")
    base_tree = _parsed(baseline)
    if base_tree is None:
        return None
    name = _import_name(source, tree, _imports(base_tree), needles, safe)
    if name is None:
        return None
    if _called(tree, name):
        return ("command", "datamodel_code_generator: extra template data reached a call")
    return ("file", "datamodel_code_generator: extra template data reached an import path")


def _source_ok(outcome) -> str | None:
    rendered = outcome.value
    if outcome.status != "ok" or type(rendered) is not str:
        return None
    if len(rendered.encode("utf-8", "surrogatepass")) > MAX_SOURCE_BYTES:
        return None
    return rendered


class World(BaseWorld):
    def load_schema(self, schema: str):
        """Generate a module from ``schema`` with no extra template data.

        Stores that module as the baseline ``generate`` compares against.
        ``outcome.injected`` stays false: there is no extra data to reach code.
        """
        text = require_text(schema, "schema", MAX_SCHEMA_BYTES)
        outcome = self.call(_generate, text, None)
        outcome.injected = False
        rendered = _source_ok(outcome)
        if rendered is None:
            self._schema = None
            self._baseline = None
            return outcome
        self._schema = text
        self._baseline = rendered
        return outcome

    def generate(self, template_data: dict):
        """Generate the loaded schema again with ``template_data``.

        ``outcome.injected`` is true only when a string from ``template_data``
        parses as a new import path or as a non-string part of a call.
        """
        schema = getattr(self, "_schema", None)
        baseline = getattr(self, "_baseline", None)
        if schema is None or baseline is None:
            raise RuntimeError("load_schema must succeed before generate")
        checked = _require_template(template_data)
        outcome = self.call(_generate, schema, checked)
        outcome.injected = False
        rendered = _source_ok(outcome)
        if rendered is None:
            return outcome
        needles: list[str] = []
        _string_leaves(checked, needles)
        if len(needles) > MAX_STRINGS:
            needles = needles[:MAX_STRINGS]
        found = _location(rendered, baseline, needles)
        if found is None:
            return outcome
        sentinel, location = found
        outcome.injected = True
        capture.injection(sentinel, location=location)
        return outcome
