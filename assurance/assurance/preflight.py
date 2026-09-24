"""Environment checks. Repositories never contain VPC addresses or credentials."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

PUBLIC_HOSTS = (
    "exchange.a2a-settlement.org",
    "mediator.a2a-settlement.org",
)


class PreflightError(Exception):
    pass


def load_lock(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "revisions" not in data:
        raise PreflightError(f"{path} is missing revisions")
    return data


def resolve_run_id(cli_run_id: str, env: dict[str, str]) -> str:
    """Use one id for probes, coverage, and execution. Reject a mismatch."""
    selected = cli_run_id.strip()
    configured = env.get("ASSURANCE_RUN_ID", "").strip()
    if selected and configured and selected != configured:
        raise PreflightError("ASSURANCE_RUN_ID does not match --run-id")
    resolved = selected or configured
    if not resolved:
        raise PreflightError("a live run requires --run-id or ASSURANCE_RUN_ID")
    return resolved


def check_online(env: dict[str, str] | None = None, *, run_id: str = "") -> list[str]:
    """Fail when a required service is unreachable. This is not the dry-run check."""
    import httpx

    env = os.environ if env is None else env
    notes = []
    for key in ("EXCHANGE_URL", "GATEWAY_URL", "BOARD_URL", "OBSERVER_URL"):
        value = env.get(key, "").strip()
        if not value:
            raise PreflightError(f"{key} is required for a live preflight")
        if any(host in value for host in PUBLIC_HOSTS):
            raise PreflightError(f"{key} points at a public default host")
        try:
            response = httpx.get(f"{value.rstrip('/')}/health", timeout=3.0)
        except httpx.HTTPError as exc:
            raise PreflightError(f"{key} health check failed: {exc}") from exc
        if response.status_code >= 400:
            raise PreflightError(f"{key} health returned {response.status_code}")
        notes.append(f"{key} health ok")
    from assurance.services import secret_from_env

    operator = secret_from_env("OPERATOR_TOKEN", "OPERATOR_TOKEN_FILE")
    run_id = resolve_run_id(run_id, env)
    if not operator:
        raise PreflightError(
            "a live preflight requires OPERATOR_TOKEN and ASSURANCE_RUN_ID"
        )
    for key, stream in (("GATEWAY_URL", "gateway"), ("BOARD_URL", "board")):
        try:
            probed = httpx.post(
                f"{env[key].rstrip('/')}/probe",
                params={"run_id": run_id},
                headers={"Authorization": f"Bearer {operator}"},
                timeout=3.0,
            )
        except httpx.HTTPError as exc:
            raise PreflightError(f"{stream} probe failed: {exc}") from exc
        if probed.status_code != 200:
            raise PreflightError(
                f"{stream} probe returned {probed.status_code} {probed.text}"
            )
        notes.append(f"{stream} probe stored for {run_id}")
    observer = env.get("OBSERVER_URL", "").rstrip("/")
    token = env.get("OBSERVER_API_KEY", "").strip()
    token_file = env.get("OBSERVER_API_KEY_FILE", "").strip()
    if token_file:
        from pathlib import Path

        token = Path(token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise PreflightError(
            "observer coverage requires OBSERVER_API_KEY or OBSERVER_API_KEY_FILE"
        )
    try:
        coverage = httpx.get(
            f"{observer}/coverage",
            params={"run_id": run_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3.0,
        )
    except httpx.HTTPError as exc:
        raise PreflightError(f"observer coverage check failed: {exc}") from exc
    if coverage.status_code >= 400:
        raise PreflightError(f"observer coverage returned {coverage.status_code}")
    body = coverage.json()
    streams = set(body.get("streams") or [])
    if body.get("run_id") != run_id:
        raise PreflightError("observer coverage is not bound to the active run")
    if body.get("complete") and streams != {"gateway", "board", "exchange"}:
        raise PreflightError(
            "observer coverage claims complete without gateway, board, and exchange streams"
        )
    if not body.get("complete"):
        raise PreflightError(
            "evidence collection is incomplete: "
            + ", ".join(body.get("gaps") or ["unknown"])
        )
    notes.append("observer coverage complete")
    return notes


def check_environment(
    env: dict[str, str] | None = None, *, lock_path: Path | None = None
) -> list[str]:
    env = os.environ if env is None else env
    label = env.get("ASSURANCE_ENV_LABEL", "").strip()
    if not label:
        raise PreflightError("ASSURANCE_ENV_LABEL is required")
    if label in {"prod", "production", "public"}:
        raise PreflightError(
            f"ASSURANCE_ENV_LABEL {label!r} is not an experiment label"
        )
    notes = [f"environment={label}"]
    for key in ("EXCHANGE_URL", "MEDIATOR_URL", "GATEWAY_URL"):
        value = env.get(key, "")
        if not value:
            continue
        if any(host in value for host in PUBLIC_HOSTS):
            raise PreflightError(f"{key} points at a public default host")
        notes.append(f"{key} is private")
    if lock_path is not None:
        lock = load_lock(lock_path)
        for name, pin in lock["revisions"].items():
            if not pin or pin.startswith("HEAD"):
                raise PreflightError(f"{name} is not pinned")
        notes.append(f"pins={len(lock['revisions'])}")
    return notes
