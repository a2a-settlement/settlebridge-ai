"""assurance preflight | run | verify-evidence"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from assurance.actors.scripted import premature_payment_actor, team_actors
from assurance.controller import run_scripted
from assurance.evidence import verify_bundle
from assurance.preflight import PreflightError, check_environment
from assurance.schemas import ScenarioSpec


def _cmd_preflight(args: argparse.Namespace) -> int:
    try:
        notes = check_environment(lock_path=Path(args.lock) if args.lock else None)
        if args.dry_run:
            print("PREFLIGHT DRY-RUN offline-only")
        else:
            from assurance.preflight import check_online

            notes.extend(check_online())
    except PreflightError as exc:
        print(f"PREFLIGHT FAIL {exc}", file=sys.stderr)
        return 1
    for note in notes:
        print(note)
    return 0


def _load_scenario(path: Path, profile: str) -> ScenarioSpec:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["control_profile"] = profile
    return ScenarioSpec.model_validate(raw)


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        check_environment()
    except PreflightError as exc:
        print(f"PREFLIGHT FAIL {exc}", file=sys.stderr)
        return 1
    scenario = _load_scenario(Path(args.scenario), args.profile)
    actors = team_actors() if args.team else [premature_payment_actor()]
    if not args.live:
        print(
            "SIMULATION in-memory payment path; this is not an exchange result",
            file=sys.stderr,
        )
        result = run_scripted(
            scenario,
            actors,
            approval_bound=args.approved,
            evidence_present=not args.drop_evidence,
            output_dir=Path(args.output),
            run_id=args.run_id,
        )
    else:
        import os

        from assurance.live import run_live
        from assurance.preflight import check_online, resolve_run_id

        try:
            run_id = resolve_run_id(args.run_id, os.environ)
            check_online(run_id=run_id)
        except PreflightError as exc:
            print(f"PREFLIGHT FAIL {exc}", file=sys.stderr)
            return 1
        from assurance.services import secret_from_env

        if args.approved:
            print(
                "CLI --approved is a setup note, not acceptance evidence",
                file=sys.stderr,
            )
        for actor in actors:
            actor.steps = [*actor.steps, *actor.steps]
        result = run_live(
            scenario,
            actors,
            gateway_url=os.environ["GATEWAY_URL"],
            exchange_url=os.environ["EXCHANGE_URL"],
            escrow_id=args.escrow_id
            or secret_from_env("ASSURANCE_ESCROW_ID", "ASSURANCE_ESCROW_ID_FILE"),
            observer_api_key=secret_from_env(
                "OBSERVER_API_KEY", "OBSERVER_API_KEY_FILE"
            ),
            evidence_present=not args.drop_evidence,
            output_dir=Path(args.output),
            run_id=run_id,
            observer_url=os.environ.get("OBSERVER_URL"),
            collector_token=secret_from_env("COLLECTOR_TOKEN", "COLLECTOR_TOKEN_FILE"),
            actor_token=secret_from_env("ACTOR_TOKEN", "ACTOR_TOKEN_FILE"),
        )
    print(result.model_dump_json(indent=2))
    return 0


def _required_secret(name: str, file_name: str) -> str:
    from assurance.services import secret_from_env

    value = secret_from_env(name, file_name)
    if not value:
        raise PreflightError(f"{name} or {file_name} is required")
    return value


def _actor_tokens() -> dict[str, str]:
    import json

    raw = _required_secret("ACTOR_TOKENS", "ACTOR_TOKENS_FILE")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not parsed:
        raise PreflightError("ACTOR_TOKENS must be a non-empty JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


def _service_collector() -> tuple[str, str, str]:
    import os

    return (
        os.environ["OBSERVER_URL"],
        _required_secret("COLLECTOR_TOKEN", "COLLECTOR_TOKEN_FILE"),
        _required_secret("OPERATOR_TOKEN", "OPERATOR_TOKEN_FILE"),
    )


def _build_gateway_from_env(_args: argparse.Namespace):
    import os

    from assurance.services import build_gateway

    observer_url, collector_token, operator_token = _service_collector()
    return build_gateway(
        os.environ["EXCHANGE_URL"],
        _required_secret("REQUESTER_API_KEY", "REQUESTER_API_KEY_FILE"),
        operator_token=operator_token,
        allowed_escrow_ids={
            _required_secret("ASSURANCE_ESCROW_ID", "ASSURANCE_ESCROW_ID_FILE")
        },
        actor_tokens=_actor_tokens(),
        observer_url=observer_url,
        collector_token=collector_token,
    )


def _build_board_from_env(_args: argparse.Namespace):
    import os

    from assurance.services import build_board

    observer_url, collector_token, operator_token = _service_collector()
    return build_board(
        os.environ.get("ASSURANCE_CHANNEL", "off"),
        operator_token=operator_token,
        actor_tokens=_actor_tokens(),
        observer_url=observer_url,
        collector_token=collector_token,
    )


def _build_observer_from_env(args: argparse.Namespace):
    from pathlib import Path

    from assurance.services import build_observer

    return build_observer(
        Path(args.evidence),
        _required_secret("OBSERVER_API_KEY", "OBSERVER_API_KEY_FILE"),
        collector_token=_required_secret("COLLECTOR_TOKEN", "COLLECTOR_TOKEN_FILE"),
    )


def _cmd_pins(args: argparse.Namespace) -> int:
    from assurance.pins import check_published_pins, check_working_tree, compose_env

    lock = Path(args.lock)
    settlement = Path(args.settlement)
    settlebridge = Path(args.settlebridge)
    try:
        if args.action == "export":
            print(
                compose_env(
                    lock,
                    settlement_context=settlement,
                    settlebridge_context=settlebridge,
                ),
                end="",
            )
            return 0
        if args.action == "check-tree":
            notes = check_working_tree(
                lock, settlement_context=settlement, settlebridge_context=settlebridge
            )
        else:
            notes = check_published_pins(
                lock, settlement_context=settlement, settlebridge_context=settlebridge
            )
    except PreflightError as exc:
        print(f"PINS FAIL {exc}", file=sys.stderr)
        return 1
    for note in notes:
        print(note)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        digest = verify_bundle(Path(args.bundle))
    except ValueError as exc:
        print(f"VERIFY FAIL {exc}", file=sys.stderr)
        return 1
    print(digest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assurance")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--lock")
    pre.add_argument("--dry-run", action="store_true")
    pre.set_defaults(func=_cmd_preflight)

    run = sub.add_parser("run")
    run.add_argument("--scenario", required=True)
    run.add_argument("--profile", choices=("baseline", "strengthened"), required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--run-id", default="run-local")
    run.add_argument("--team", action="store_true")
    run.add_argument("--approved", action="store_true")
    run.add_argument("--drop-evidence", action="store_true")
    run.add_argument("--live", action="store_true")
    run.add_argument("--escrow-id", default="")
    run.set_defaults(func=_cmd_run)

    def _serve(builder):
        def command(args: argparse.Namespace) -> int:
            from assurance.services import serve

            serve(builder(args), args.host, args.port)
            return 0

        return command

    gateway = sub.add_parser("serve-gateway")
    gateway.add_argument("--host", default="0.0.0.0")
    gateway.add_argument("--port", type=int, default=8000)
    gateway.set_defaults(func=_serve(_build_gateway_from_env))

    board = sub.add_parser("serve-board")
    board.add_argument("--host", default="0.0.0.0")
    board.add_argument("--port", type=int, default=8000)
    board.set_defaults(func=_serve(_build_board_from_env))

    observer = sub.add_parser("serve-observer")
    observer.add_argument("--host", default="0.0.0.0")
    observer.add_argument("--port", type=int, default=8000)
    observer.add_argument("--evidence", default="/evidence")
    observer.set_defaults(func=_serve(_build_observer_from_env))

    pins = sub.add_parser("pins")
    pins.add_argument("action", choices=("export", "check-tree", "check-pin"))
    pins.add_argument("--lock", required=True)
    pins.add_argument("--settlement", required=True)
    pins.add_argument("--settlebridge", required=True)
    pins.set_defaults(func=_cmd_pins)

    verify = sub.add_parser("verify-evidence")
    verify.add_argument("bundle")
    verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
