"""Local driver for the PR #32 challenge pilot.

Runs the real challenge engine against a local Postgres and the local gVisor sandbox.
It fills two operator steps that have no route yet (setting the task environment and
freezing the contract) and runs the platform challenger for the attempt job.

Run with the backend interpreter, for example:

    python challenge-runner/pilots/pr32/drive.py setup
    python challenge-runner/pilots/pr32/drive.py run --model <openai-model>
    python challenge-runner/pilots/pr32/drive.py claims
    python challenge-runner/pilots/pr32/drive.py status
    python challenge-runner/pilots/pr32/drive.py adjudicate --claim <id> --verdict <v> --reason <text>
    python challenge-runner/pilots/pr32/drive.py report

Secrets are read from files and never printed: the OpenAI key from
~/.config/settlebridge-pilot/openai_key and the GitHub token from `gh auth token`.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BACKEND = REPO / "backend"
RUNNER = REPO / "challenge-runner"
HOME = Path(
    os.environ.get("SBC_PILOT_HOME", str(Path.home() / ".local/share/settlebridge-pilot/pr32"))
)
SECRETS = Path.home() / ".config/settlebridge-pilot"
STATE = HOME / "state.json"
IMAGE = "python@sha256:65bd3d9f1d7fa268dd1909973ba7eb4bdf41f6dcf2cfbe76ba9528b170f3fd79"
DATABASE_URL = os.environ.get(
    "SBC_PILOT_DATABASE_URL",
    "postgresql+asyncpg://pilot:pilot@127.0.0.1:55432/settlebridge_pilot",
)
IDEMPOTENCY_KEY = "pilot-pr32-v{}"
BUDGET_CENTS = 2500
MAX_CLAIMS = 3
MAX_STEPS = 60


def _load_state() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text())
    return {}


def _save_state(state: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _signing_key() -> str:
    path = SECRETS / "report_signing_key.pem"
    if not path.is_file():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        SECRETS.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(pem)
    return path.read_text()


def _configure() -> dict:
    state = _load_state()
    state.setdefault("operator_user_id", str(uuid.uuid4()))
    _save_state(state)
    os.environ.update(
        {
            "DATABASE_URL": DATABASE_URL,
            "DEBUG": "false",
            "MARKETPLACE_ENABLED": "true",
            "CHALLENGE_ENGINE_ENABLED": "true",
            "CHALLENGE_ARTIFACT_ROOT": str(HOME / "artifacts"),
            "CHALLENGE_RUNNER_PYTHON": sys.executable,
            "CHALLENGE_SANDBOX_IMAGE": IMAGE,
            "CHALLENGE_SANDBOX_RUNTIME": "runsc",
            "CHALLENGE_REPORT_SIGNING_KEY": _signing_key(),
            "CHALLENGE_REPORT_KEY_ID": "pilot-local-1",
            "CHALLENGE_OPERATOR_USER_IDS": state["operator_user_id"],
        }
    )
    sys.path[:0] = [str(BACKEND), str(RUNNER)]
    return state


def _store():
    from challenge_runner.artifacts import ArtifactStore

    return ArtifactStore(HOME / "artifacts")


def _fixtures_tarball() -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        root = HERE / "verifier"
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if "__pycache__" in rel.parts or path.suffix == ".pyc":
                continue
            info = tar.gettarinfo(str(path), arcname=rel.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
            else:
                tar.addfile(info)
    return gzip.compress(raw.getvalue(), mtime=0)


def _migrate() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True
    )


async def _setup(state: dict) -> None:
    from app.database import async_session
    from sqlalchemy import select

    from app.models.challenge import ChallengeTask
    from app.models.spend import AccountBudget
    from app.models.user import User, UserType
    from app.schemas.challenge import ChallengeTaskCreate
    from app.services import challenge_orchestrator as orch

    contract = json.loads((HERE / "contract.json").read_text())
    store = _store()
    fixtures = store.put_bytes(_fixtures_tarball())
    canaries = store.put_bytes((HERE / "verifier" / "canaries.json").read_bytes())
    operator = uuid.UUID(state["operator_user_id"])
    async with async_session() as db:
        if await db.get(User, operator) is None:
            db.add(
                User(
                    id=operator,
                    email="pilot-operator@settlebridge.invalid",
                    password_hash="!no-login",
                    display_name="Rich (pilot operator)",
                    user_type=UserType.REQUESTER,
                )
            )
            await db.flush()
        budget = (
            await db.execute(select(AccountBudget).where(AccountBudget.user_id == operator))
        ).scalar_one_or_none()
        if budget is None:
            db.add(
                AccountBudget(
                    id=uuid.uuid4(),
                    user_id=operator,
                    period_cap_cents=BUDGET_CENTS,
                    per_job_cap_cents=BUDGET_CENTS,
                    max_concurrent_jobs=1,
                    period_start=datetime.now(timezone.utc),
                )
            )
            await db.flush()
        body = ChallengeTaskCreate(
            **contract["task"],
            deadline_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        existing = state.get("task_id")
        if existing:
            task = await db.get(ChallengeTask, uuid.UUID(existing))
        else:
            task = await orch.create_task(
                db,
                owner_user_id=operator,
                installation_id=int(os.environ.get("SBC_PILOT_INSTALLATION_ID", "0")),
                body=body,
                idempotency_key=IDEMPOTENCY_KEY.format(state.setdefault("run", 1)),
            )
        if task.state == "draft":
            task.environment = {
                **contract["environment"],
                "fixtures_path": str(store.get_path(fixtures)),
                "canaries_digest": canaries,
            }
            await db.flush()
            await orch.freeze_contract(db, task.id)
        await db.commit()
        state["task_id"] = str(task.id)
        _save_state(state)
        print(f"task {task.id} is {task.state}")


def _gh_token() -> str:
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    token = out.stdout.strip()
    if not token:
        raise SystemExit("gh auth token returned nothing; run `gh auth login` first")
    return token


def _model_client(model: str, reasoning: bool):
    from challenge_runner.challenger.http_client import ChatCompletionsClient

    key_file = SECRETS / "openai_key"
    if not key_file.is_file():
        raise SystemExit(f"missing {key_file}; save the OpenAI key there (chmod 600)")
    key = key_file.read_text().strip()
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


def _diff(base: str, target: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--no-color", "--no-ext-diff", base, target],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


async def _frozen_bundle(db, job, work: Path):
    """Extract the frozen target source: the only repository view either model gets."""
    from sqlalchemy import select

    from app.models.challenge import ChallengeArtifact, ChallengeContract, ChallengeTask
    from challenge_runner.challenger import ChallengeBundle
    from challenge_runner.sandbox.tarutil import safe_extract

    task = await db.get(ChallengeTask, uuid.UUID(job.payload["task_id"]))
    contract = await db.get(ChallengeContract, uuid.UUID(job.payload["contract_id"]))
    rows = await db.execute(
        select(ChallengeArtifact).where(
            ChallengeArtifact.task_id == task.id,
            ChallengeArtifact.kind == "source_tarball",
        )
    )
    target = next(row for row in rows.scalars() if (row.meta or {}).get("role") == "target")
    shutil.rmtree(work, ignore_errors=True)
    source = work / "src"
    source.mkdir(parents=True, exist_ok=True)
    safe_extract(Path(target.storage_path), source, max_bytes=512 * 1024 * 1024)
    return ChallengeBundle(
        contract=contract.content,
        source_dir=source,
        diff_text=_diff(task.base_sha, task.target_sha),
    )


async def _prepare_validations(args) -> None:
    """Give each pending validation an independent re-derivation from the scenario alone."""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.challenge import ChallengeClaim
    from app.models.github import GitHubJob, JobKind, JobStatus
    from challenge_runner.challenger.rederive import run_rederiver

    async with async_session() as db:
        stmt = select(GitHubJob).where(
            GitHubJob.kind == JobKind.CHALLENGE_VALIDATE.value,
            GitHubJob.status == JobStatus.PENDING.value,
        )
        for job in (await db.execute(stmt)).scalars().all():
            if "rederive_attempted" in (job.payload or {}):
                continue
            model = args.rederive_model or args.model
            if not model:
                raise SystemExit("a claim is ready to validate; rerun with --model <openai-model>")
            claim = await db.get(ChallengeClaim, uuid.UUID(job.payload["claim_id"]))
            work = HOME / "rederiver" / str(job.id)
            bundle = await _frozen_bundle(db, job, work)
            client = _model_client(model, not args.chat_model)
            print(f"re-deriver: one call to {model} with the scenario only")
            result = run_rederiver(
                bundle,
                {
                    "failure_class": claim.failure_class,
                    "scenario": claim.scenario,
                    "oracle_citation": claim.oracle_citation,
                },
                out_dir=work / "out",
                model_client=client,
            )
            usage = client.last_usage or {}
            (work / "model_usage.json").write_text(
                json.dumps({"model": model, **usage}, indent=2) + "\n"
            )
            payload = {**job.payload, "rederive_attempted": True}
            if result is not None:
                digest = _store().put_file(result.bundle_path)
                payload["rederive_bundle_path"] = str(_store().get_path(digest))
                payload["rederive_test_path"] = result.test_path
            job.payload = payload
            await db.commit()
            print(
                f"re-deriver: {'wrote ' + result.test_path if result else 'no usable test'}; usage {usage}"
            )


async def _prepare_attempts(args) -> None:
    from sqlalchemy import select

    from app.database import async_session
    from app.models.github import GitHubJob, JobKind, JobStatus
    from challenge_runner.challenger import run_challenger
    from challenge_runner.challenger.limits import ARCHIVE_NAME, MAX_COMPLETION_TOKENS

    async with async_session() as db:
        stmt = select(GitHubJob).where(
            GitHubJob.kind == JobKind.CHALLENGE_ATTEMPT.value,
            GitHubJob.status == JobStatus.PENDING.value,
        )
        for job in (await db.execute(stmt)).scalars().all():
            if "bundle_path" in (job.payload or {}):
                continue
            if not args.model:
                raise SystemExit("the attempt is ready; rerun with --model <openai-model>")
            work = HOME / "challenger" / str(job.id)
            bundle = await _frozen_bundle(db, job, work)
            client = _model_client(args.model, not args.chat_model)
            print(f"challenger: one call to {args.model}, at most {MAX_CLAIMS} claims")
            claims = run_challenger(
                bundle, out_dir=work / "out", model_client=client, max_claims=MAX_CLAIMS
            )
            usage = client.last_usage or {}
            (work / "model_usage.json").write_text(
                json.dumps({"model": args.model, **usage}, indent=2) + "\n"
            )
            if not claims and usage.get("output_tokens", 0) >= MAX_COMPLETION_TOKENS:
                raise SystemExit(
                    "the model hit its output cap with no claims; the attempt was not submitted"
                )
            archive = next((work / "out").rglob(ARCHIVE_NAME))
            digest = _store().put_file(archive)
            job.payload = {
                **job.payload,
                "claims": [asdict(claim) for claim in claims],
                "bundle_path": str(_store().get_path(digest)),
            }
            await db.commit()
            print(f"challenger: {len(claims)} claim(s); usage {usage}")


async def _job_problems() -> list[str]:
    from sqlalchemy import select

    from app.database import async_session
    from app.models.github import GitHubJob

    async with async_session() as db:
        rows = (await db.execute(select(GitHubJob))).scalars().all()
    return [
        f"{row.kind} {row.status} attempts={row.attempts} error={getattr(row, 'last_error', '')}"
        for row in rows
        if row.status not in ("pending", "succeeded", "done", "completed")
    ]


async def _pending_jobs() -> int:
    from sqlalchemy import func, select

    from app.database import async_session
    from app.models.github import GitHubJob, JobStatus

    async with async_session() as db:
        stmt = select(func.count()).where(GitHubJob.status == JobStatus.PENDING.value)
        return int((await db.execute(stmt)).scalar_one())


async def _task_state(state: dict) -> str:
    from app.database import async_session
    from app.models.challenge import ChallengeTask

    async with async_session() as db:
        task = await db.get(ChallengeTask, uuid.UUID(state["task_id"]))
        return task.state


def _keep_runner_logs() -> None:
    from app.services import challenge_runner_client

    original = challenge_runner_client._run
    logs = HOME / "runner-logs"

    def logged(cmd, *, input_text=None):
        done = original(cmd, input_text=input_text)
        if done.returncode != 0 or done.stderr.strip():
            logs.mkdir(parents=True, exist_ok=True)
            path = logs / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}-{cmd[3]}.log"
            path.write_text(f"exit {done.returncode}\n{done.stderr}")
            print(f"runner {cmd[3]} exited {done.returncode}; stderr in {path}")
        return done

    challenge_runner_client._run = logged


async def _run(state: dict, args) -> None:
    from app.database import async_session
    from app.services import github_jobs, github_scoped_token

    async def scoped_read_token(installation_id: int, repo_full_name: str) -> str:
        return _gh_token()

    github_scoped_token.scoped_read_token = scoped_read_token
    _keep_runner_logs()
    idle = 0
    for _ in range(MAX_STEPS):
        await _prepare_attempts(args)
        if args.challenger_only:
            print("stopped before validation; review with `drive.py claims`")
            return
        await _prepare_validations(args)
        ran = await github_jobs.run_due_jobs(async_session, limit=1)
        print(f"ran {ran} job(s); task is {await _task_state(state)}")
        if ran:
            idle = 0
            continue
        if not await _pending_jobs():
            break
        idle += 1
        if idle > 6:
            print("jobs are waiting on a retry delay; run again later")
            break
        await asyncio.sleep(10)
    for problem in await _job_problems():
        print(f"job problem: {problem}")


async def _detail(state: dict) -> dict:
    from app.database import async_session
    from app.services import challenge_report

    async with async_session() as db:
        detail = await challenge_report.challenge_detail(
            db, uuid.UUID(state["task_id"]), uuid.UUID(state["operator_user_id"])
        )
    return json.loads(json.dumps(detail, default=_jsonable))


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _claims() -> None:
    """Print the challenger's claims and test files without any verdict, for blind review."""
    for out in sorted((HOME / "challenger").glob("*/out")):
        print(f"== {out.parent.name}")
        print((out / "claims.json").read_text())
        print("rejected:", (out / "rejected.json").read_text().strip())
        for path in sorted(out.rglob("challenge_tests/**/*")):
            if path.is_file():
                print(f"--- {path.relative_to(out)}")
                print(path.read_text(errors="replace"))


async def _adjudicate(state: dict, args) -> None:
    from sqlalchemy import select

    from app.database import async_session
    from app.models.challenge import ChallengeClaim
    from app.services import challenge_orchestrator as orch

    async with async_session() as db:
        claim_id = None
        rows = await db.execute(
            select(ChallengeClaim.id).where(ChallengeClaim.task_id == uuid.UUID(state["task_id"]))
        )
        for row in rows.scalars():
            if str(row).startswith(args.claim):
                claim_id = row
        if claim_id is None:
            raise SystemExit("no claim on this task matches that id")
        await orch._record_adjudication(
            db,
            task_id=uuid.UUID(state["task_id"]),
            claim_id=claim_id,
            operator_id=state["operator_user_id"],
            verdict=args.verdict,
            reason=args.reason,
        )
        await db.commit()
    print(f"recorded {args.verdict} for claim {claim_id}; run `drive.py run` to build the report")


async def _report(state: dict) -> None:
    from app.config import settings
    from app.database import async_session
    from app.services import challenge_report

    async with async_session() as db:
        report = await challenge_report.build_report(db, uuid.UUID(state["task_id"]))
    token = challenge_report.sign_report(report, settings.CHALLENGE_REPORT_SIGNING_KEY)
    (HOME / "report.jws").write_text(token + "\n")
    body = report.model_dump(mode="json") if hasattr(report, "model_dump") else report
    (HOME / "report.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    print(f"wrote {HOME / 'report.json'} and {HOME / 'report.jws'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup")
    setup.add_argument("--fresh", action="store_true", help="start a new task")
    run = sub.add_parser("run")
    run.add_argument("--model", default=os.environ.get("SBC_PILOT_MODEL", ""))
    run.add_argument(
        "--rederive-model",
        default="",
        help="model for the independent re-derivation (default: --model)",
    )
    run.add_argument(
        "--chat-model",
        action="store_true",
        help="send max_tokens and temperature 0 (non-reasoning models)",
    )
    run.add_argument(
        "--challenger-only",
        action="store_true",
        help="run the challenger for a ready attempt, then stop before validation",
    )
    sub.add_parser("claims")
    sub.add_parser("status")
    adjudicate = sub.add_parser("adjudicate")
    adjudicate.add_argument("--claim", required=True, help="claim id or unique prefix")
    adjudicate.add_argument(
        "--verdict",
        required=True,
        choices=["confirmed", "no_failure_found", "inconclusive", "invalid_or_duplicate"],
    )
    adjudicate.add_argument("--reason", required=True)
    sub.add_parser("report")
    args = parser.parse_args()

    state = _configure()
    if args.command == "setup":
        if args.fresh and "task_id" in state:
            state.pop("task_id")
            state["run"] = state.get("run", 1) + 1
            _save_state(state)
        _migrate()
        asyncio.run(_setup(state))
        return
    if "task_id" not in state:
        raise SystemExit("run `drive.py setup` first")
    if args.command == "run":
        asyncio.run(_run(state, args))
    elif args.command == "claims":
        _claims()
    elif args.command == "status":
        print(json.dumps(asyncio.run(_detail(state)), indent=2, sort_keys=True))
    elif args.command == "adjudicate":
        asyncio.run(_adjudicate(state, args))
    elif args.command == "report":
        asyncio.run(_report(state))


if __name__ == "__main__":
    main()
