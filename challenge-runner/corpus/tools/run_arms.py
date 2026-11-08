"""Run the challenge arms over corpus slice entries and emit scorer result rows.

This is a coordinator tool, not the production engine: it drives the real
challenger and re-deriver models and the real ``challenge_validity.decide`` over
the kit, without the orchestrator/Postgres/job machinery. It exists to measure
cost and early numbers on the slice.

    python corpus/tools/run_arms.py --entry pyyaml-fullloader-rce --dry-run
    python corpus/tools/run_arms.py --all --out /tmp/sbc-corpus/results.jsonl

Secrets: the OpenAI key is read from ~/.config/settlebridge-pilot/openai_key and
never printed. Real model spend happens only for the seeded_challenger and
second_model_review arms.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parents[1]  # challenge-runner/
BACKEND = RUNNER.parent / "backend"
CHECKOUTS = Path(os.environ.get("SBC_CORPUS_CHECKOUTS", "/tmp/sbc-corpus/checkouts"))
FLASK_DEPS = Path(os.environ.get("SBC_FLASK_DEPS", "/tmp/sbc-corpus/flaskdeps"))
SECRETS = Path.home() / ".config/settlebridge-pilot"
PY = os.environ.get("SBC_PY", sys.executable)
ZERO_DIGEST = "sha256:" + "0" * 64

# base/target/followup commit dir per entry, under CHECKOUTS.
CHECKOUT_IDS = {
    "pyyaml-fullloader-rce": ("pyyaml-2f463cf5b", "pyyaml-5080ba513"),
    "jinja-xmlattr-keys": ("jinja-d84a1743e", "jinja-716795349"),
    "mako-lexer-redos": ("mako-7c5b28ac4", "mako-925760291"),
    "flask-session-vary-cookie": ("flask-9532cba45", "flask-70f906c51"),
    "waitress-header-regex-redos": ("waitress-ddb65b489", "waitress-0bf98dadd"),
    "pyjwt-options-mutation": ("pyjwt-9085670", "pyjwt-29fbfc3"),
}

# Assumed prices (US cents per 1M tokens); report tokens so actual billing rules.
PRICE_IN_CENTS_PER_M = float(os.environ.get("SBC_PRICE_IN", "250"))
PRICE_OUT_CENTS_PER_M = float(os.environ.get("SBC_PRICE_OUT", "1000"))
COMPUTE_CENTS_PER_WALL_SEC = float(os.environ.get("SBC_COMPUTE_RATE", "0.5"))


def _ensure_paths() -> None:
    sys.path[:0] = [str(BACKEND), str(RUNNER)]


def _openai_key() -> str:
    path = SECRETS / "openai_key"
    if not path.is_file():
        raise SystemExit(f"missing {path}; save the OpenAI key there (chmod 600)")
    return path.read_text().strip()


def _client(model: str, reasoning: bool):
    from challenge_runner.challenger.http_client import ChatCompletionsClient

    key = _openai_key()
    if reasoning:
        return ChatCompletionsClient(
            "https://api.openai.com/v1",
            model,
            key,
            timeout=600,
            max_tokens_field="max_completion_tokens",
            temperature=None,
        )
    return ChatCompletionsClient("https://api.openai.com/v1", model, key, timeout=600)


def _usage_cents(usage: dict | None) -> tuple[int, int, float]:
    usage = usage or {}
    tin = int(usage.get("input_tokens", 0) or 0)
    tout = int(usage.get("output_tokens", 0) or 0)
    cents = tin / 1e6 * PRICE_IN_CENTS_PER_M + tout / 1e6 * PRICE_OUT_CENTS_PER_M
    return tin, tout, cents


def _contract_content(contract: dict) -> dict:
    task = contract["task"]
    env = dict(contract["environment"])
    for key in ("author_operator_id", "solver_operator_id"):
        env.pop(key, None)
    oracle_sources = env.pop("oracle_sources", [])
    oracle_texts = env.pop("oracle_texts", {})
    observers = env.pop("observers", [])
    return {
        "property": task["property_text"],
        "failure_classes": list(task["failure_classes"]),
        "oracle_sources": oracle_sources,
        "oracle_texts": oracle_texts,
        "observers": observers,
        "environment": env,
        "budget_cents": int(task["budget_cents"]),
        "base_sha": task["base_sha"],
        "target_sha": task["target_sha"],
    }


def _verifier_root(entry_dir: Path) -> Path:
    root = Path(tempfile.mkdtemp(prefix="sbc-arms-vroot-"))
    kit = RUNNER / "corpus" / "kit"
    shutil.copy(kit / "run.py", root / "run.py")
    shutil.copy(kit / "pytest.ini", root / "pytest.ini")
    shutil.copytree(kit / "sbc_kit", root / "sbc_kit")
    shutil.copy(entry_dir / "entry.json", root / "entry.json")
    shutil.copy(entry_dir / "sbc_entry_world.py", root / "sbc_entry_world.py")
    shutil.copytree(entry_dir / "controls", root / "controls")
    return root


def _pythonpath(entry_id: str) -> str:
    parts = []
    if entry_id == "flask-session-vary-cookie" and FLASK_DEPS.is_dir():
        parts.append(str(FLASK_DEPS))
    return os.pathsep.join([*parts, os.environ.get("PYTHONPATH", "")]).strip(os.pathsep)


def _run_kit(vroot: Path, work: Path, test_paths: list[str], entry_id: str):
    capture = Path(tempfile.mkdtemp(prefix="sbc-arms-cap-"))
    env = {
        **os.environ,
        "SBC_VERIFIER_ROOT": str(vroot),
        "SBC_WORK_DIR": str(work),
        "SBC_CAPTURE_DIR": str(capture),
        "PYTHONPATH": _pythonpath(entry_id),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    started = time.monotonic()
    proc = subprocess.run(
        [PY, str(vroot / "run.py"), *test_paths],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(work),
    )
    wall = time.monotonic() - started
    return proc.returncode, capture, wall


def _runner_result(failure_class: str, exit_code: int, capture: Path):
    from challenge_runner.observers import get_observer
    from challenge_runner.protocol import RunnerResult

    status = "ok" if exit_code == 0 else "test_failed" if exit_code == 1 else "sandbox_error"
    events = tuple(get_observer(failure_class).observe(str(capture)))
    return RunnerResult(
        execution_id=f"arms:{uuid.uuid4().hex[:12]}",
        stage="execute",
        status=status,
        image_digest=ZERO_DIGEST,
        source_digest=ZERO_DIGEST,
        exit_code=exit_code,
        observer_events=events,
    )


def _diff(base_dir: Path, target_dir: Path) -> str:
    proc = subprocess.run(
        ["diff", "-ru", str(base_dir), str(target_dir)],
        capture_output=True,
        text=True,
    )
    return proc.stdout[: 32 * 1024]


def _place_test(bundle_tests: Path, work: Path) -> None:
    dst = work / "challenge_tests"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(bundle_tests, dst)


def run_challenger_arm(entry: dict, contract: dict, model: str, reasoning: bool, verbose: bool):
    from challenge_runner.challenger import ChallengeBundle, run_challenger

    eid = entry["id"]
    fc = entry["failure_class"]
    base_dir = CHECKOUTS / CHECKOUT_IDS[eid][0]
    target_dir = CHECKOUTS / CHECKOUT_IDS[eid][1]
    content = _contract_content(contract)
    vroot = _verifier_root(RUNNER / "corpus" / "entries" / eid)
    work_root = Path(tempfile.mkdtemp(prefix="sbc-arms-work-"))

    # Fresh copies of base and target so we can drop in challenge_tests.
    base_work = work_root / "base"
    target_work = work_root / "target"
    shutil.copytree(base_dir, base_work)
    shutil.copytree(target_dir, target_work)
    diff = _diff(base_dir, target_dir)

    totals = {"in": 0, "out": 0, "cents": 0.0, "wall": 0.0, "calls": 0}

    bundle = ChallengeBundle(contract=content, source_dir=target_work, diff_text=diff)
    client = _client(model, reasoning)
    out_dir = work_root / "challenger-out"
    claims = run_challenger(bundle, out_dir=out_dir, model_client=client, max_claims=3)
    tin, tout, cents = _usage_cents(client.last_usage)
    totals["in"] += tin
    totals["out"] += tout
    totals["cents"] += cents
    totals["calls"] += 1
    if verbose:
        print(f"[{eid}] challenger: {len(claims)} claim(s); usage in={tin} out={tout}")

    verdict = "no_failure_found"
    controls_ok = True
    if claims:
        verdict, controls_ok = _validate_first_claim(
            eid,
            fc,
            content,
            claims,
            out_dir,
            vroot,
            base_work,
            target_work,
            work_root,
            model,
            reasoning,
            totals,
            verbose,
        )

    totals["cents"] += totals["wall"] * COMPUTE_CENTS_PER_WALL_SEC
    row = {
        "entry_id": eid,
        "arm": "seeded_challenger",
        "verdict": verdict,
        "audit": None,
        "controls_ok": bool(controls_ok),
        "oracle_tamper_accepted": False,
        "compute_cents": int(round(totals["cents"])),
    }
    return row, totals


def _validate_first_claim(
    eid,
    fc,
    content,
    claims,
    out_dir,
    vroot,
    base_work,
    target_work,
    work_root,
    model,
    reasoning,
    totals,
    verbose,
):
    from app.schemas.challenge import ClaimIn
    from app.services import challenge_validity
    from challenge_runner.challenger import ChallengeBundle
    from challenge_runner.challenger.rederive import run_rederiver

    claim = claims[0]
    claim_in = ClaimIn.model_validate(
        {
            "failure_class": claim.failure_class,
            "scenario": claim.scenario,
            "oracle_citation": claim.oracle_citation,
            "expected": claim.expected,
            "observed": claim.observed,
            "claimed_impact": claim.claimed_impact,
        }
    )
    test_path = claim.scenario["test_path"]
    bundle_tests = out_dir / "challenge_tests"

    def run(work, paths):
        code, cap, wall = _run_kit(vroot, work, paths, eid)
        totals["wall"] += wall
        return _runner_result(fc, code, cap)

    positive = run(target_work, [str(vroot / "controls" / "test_positive.py")])
    negative = run(target_work, [str(vroot / "controls" / "test_negative.py")])

    _place_test(bundle_tests, target_work)
    _place_test(bundle_tests, base_work)
    target = run(target_work, [test_path])
    base = run(base_work, [test_path])

    # Independent re-derivation: a second model call sees the scenario only.
    rederivation = None
    diff = _diff(base_work, target_work)
    rbundle = ChallengeBundle(contract=content, source_dir=target_work, diff_text=diff)
    rclient = _client(model, reasoning)
    rout = work_root / "rederive-out"
    result = run_rederiver(
        rbundle,
        {
            "failure_class": claim.failure_class,
            "scenario": claim.scenario,
            "oracle_citation": claim.oracle_citation,
        },
        out_dir=rout,
        model_client=rclient,
    )
    tin, tout, cents = _usage_cents(rclient.last_usage)
    totals["in"] += tin
    totals["out"] += tout
    totals["cents"] += cents
    totals["calls"] += 1
    if result is not None:
        _place_test(rout / "challenge_tests", target_work)
        rederivation = run(target_work, [result.test_path])
        _place_test(bundle_tests, target_work)  # restore the challenger test
    if verbose:
        print(
            f"[{eid}] rederive: {'wrote ' + result.test_path if result else 'none'}; "
            f"usage in={tin} out={tout}"
        )

    reruns = [run(target_work, [test_path]) for _ in range(3)]
    minimized = run(target_work, [test_path])

    outcome = challenge_validity.decide(
        claim=claim_in,
        contract_content=content,
        tamper_clean=True,
        target=target,
        positive=positive,
        negative=negative,
        base=base,
        reference=None,
        rederivation=rederivation,
        reruns=reruns,
        minimized=minimized,
    )
    controls_ok = (
        positive.status == "ok"
        and negative.status in {"ok", "test_failed"}
        and not negative.observer_events
    )
    if verbose:
        print(f"[{eid}] verdict={outcome.verdict} reason={outcome.reason!r}")
    return outcome.verdict, controls_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entry", help="run a single entry by id")
    parser.add_argument("--all", action="store_true", help="run every slice entry")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--chat-model", action="store_true", help="non-reasoning model")
    parser.add_argument(
        "--dry-run", action="store_true", help="one entry, print cost, no results file"
    )
    parser.add_argument("--out", default="/tmp/sbc-corpus/results.jsonl")
    parser.add_argument("--cap-cents", type=float, default=5000.0, help="stop before this spend")
    args = parser.parse_args()

    _ensure_paths()
    contracts_dir = RUNNER / "corpus" / "contracts"
    manifest = {
        e["id"]: e for e in json.loads((RUNNER / "corpus" / "manifest.json").read_text())["entries"]
    }

    if args.entry:
        ids = [args.entry]
    elif args.all:
        ids = list(CHECKOUT_IDS)
    else:
        raise SystemExit("pass --entry <id> or --all")

    reasoning = not args.chat_model
    rows = []
    spent = 0.0
    for eid in ids:
        contract = json.loads((contracts_dir / f"{eid}.json").read_text())
        row, totals = run_challenger_arm(manifest[eid], contract, args.model, reasoning, True)
        rows.append(row)
        spent += totals["cents"]
        print(
            f"== {eid}: verdict={row['verdict']} controls_ok={row['controls_ok']} "
            f"compute_cents={row['compute_cents']} | tokens in={totals['in']} out={totals['out']} "
            f"calls={totals['calls']} est_model_cents={totals['cents'] - totals['wall'] * COMPUTE_CENTS_PER_WALL_SEC:.1f}"
        )
        print(f"   cumulative est spend ~= {spent:.1f} cents (cap {args.cap_cents})")
        if spent >= args.cap_cents:
            print("   reached cap; stopping")
            break

    if not args.dry_run:
        Path(args.out).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
