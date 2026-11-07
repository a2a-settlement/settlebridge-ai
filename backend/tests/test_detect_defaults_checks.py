"""detect_defaults proposes test and CI checks, not dependency or Pages automation."""

# ruff: noqa: E402

from __future__ import annotations

import sys
import types

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from sqlalchemy.orm import Session

from app.services.github_onboarding import detect_defaults
from tests.test_github_onboarding import ACTIONS, REPO, Bridge, FakeGitHub, _run, make_engine, seed

KEPT = (
    {"name": "ci / build", "app_id": ACTIONS},
    {"name": "dependency-review", "app_id": ACTIONS},
    {"name": "pytest (3.11)", "app_id": ACTIONS},
    {"name": "test", "app_id": ACTIONS},
)


def _check(name: str, *, slug: str = "github-actions", app_id: int = ACTIONS) -> dict:
    return {"name": name, "conclusion": "success", "app": {"id": app_id, "slug": slug}}


@pytest.fixture()
def db():
    eng = make_engine()
    bridge = Bridge(Session(eng, expire_on_commit=False))
    seed(bridge.sync)
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


@pytest.fixture()
def gh(monkeypatch):
    return FakeGitHub().install(monkeypatch)


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("update-pip-graph", "github-actions"),
        ("Update-NPM-Graph", "github-actions"),
        ("update-go_modules-graph", "github-actions"),
        ("UPDATE-MAVEN-GRAPH", "github-actions"),
        ("dependency-graph", "github-actions"),
        ("Upload dependency-graph snapshot", "github-actions"),
        ("dependency graph", "github-actions"),
        ("Refresh Dependency Graph", "github-actions"),
        ("dependency submission", "github-actions"),
        ("pip dependency submission", "github-actions"),
        ("automatic dependency submission", "github-actions"),
        ("Automatic Dependency Submission", "github-actions"),
        ("Dependabot", "github-actions"),
        ("dependabot updates", "github-actions"),
        ("  Dependabot Updates  ", "github-actions"),
        ("pytest (3.11)", "dependabot"),
        ("pytest (3.11)", "Dependabot"),
        ("pages build and deployment", "github-pages"),
        ("Pages Build and Deployment", "github-actions"),
    ],
)
def test_detect_defaults_omits_each_nontest_pattern(db, gh, name, slug):
    gh.runs = [_check(name, slug=slug), _check("test")]
    detected = _run(detect_defaults(db, REPO))
    assert detected["candidate_checks"] == [{"name": "test", "app_id": ACTIONS}]


def test_detect_defaults_keeps_real_checks_in_candidate_order(db, gh):
    gh.runs = [
        _check("update-pip-graph"),
        _check("test"),
        _check("Pages Build and Deployment"),
        _check("pytest (3.11)"),
        _check("Dependabot"),
        _check("Dependency Graph"),
        _check("pip dependency submission"),
        _check("automatic dependency submission"),
        _check("dependency-review"),
        _check("ci / still running", slug="dependabot"),
        _check("ci / build"),
    ]
    detected = _run(detect_defaults(db, REPO))
    assert detected["candidate_checks"] == list(KEPT)


def test_detect_defaults_returns_no_candidates_when_every_run_is_excluded(db, gh):
    gh.runs = [
        _check("update-pip-graph"),
        _check("Update-Go-Graph"),
        _check("dependency-graph"),
        _check("Dependency Graph"),
        _check("dependency submission"),
        _check("automatic dependency submission"),
        _check("Dependabot"),
        _check("ci / build", slug="dependabot"),
        _check("pages build and deployment"),
    ]
    detected = _run(detect_defaults(db, REPO))
    assert detected["candidate_checks"] == []
    assert detected["required_checks"] == [{"name": "test", "app_id": ACTIONS}]
