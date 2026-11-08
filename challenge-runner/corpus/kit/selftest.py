"""Self-test a corpus entry's verifier against checked-out library commits.

For each commit it runs the positive control, the negative control, and a stand-in
attack test through the real kit (forked clean import + audit hook), then runs the
frozen observer over each capture directory and prints what fired.

    python selftest.py <entry_dir> <attack_test.py> <failure_class> name=commit_dir ...
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PY = os.environ.get("SBC_PY", "/home/rich/miniconda3/envs/settlebridge_ai/bin/python")


def _runner_root(entry_dir: Path) -> Path:
    # entries/<id> -> corpus -> challenge-runner
    return entry_dir.resolve().parents[2]


def build_verifier_root(entry_dir: Path) -> Path:  # noqa: D103
    root = Path(tempfile.mkdtemp(prefix="sbc-verifier-"))
    kit = _runner_root(entry_dir) / "corpus" / "kit"
    shutil.copy(kit / "run.py", root / "run.py")
    shutil.copy(kit / "pytest.ini", root / "pytest.ini")
    shutil.copytree(kit / "sbc_kit", root / "sbc_kit")
    shutil.copy(entry_dir / "entry.json", root / "entry.json")
    shutil.copy(entry_dir / "sbc_entry_world.py", root / "sbc_entry_world.py")
    shutil.copytree(entry_dir / "controls", root / "controls")
    return root


def run_stage(verifier_root: Path, work: Path, test_paths: list[str]) -> tuple[int, Path, str]:
    capture = Path(tempfile.mkdtemp(prefix="sbc-capture-"))
    env = {
        **os.environ,
        "SBC_VERIFIER_ROOT": str(verifier_root),
        "SBC_WORK_DIR": str(work),
        "SBC_CAPTURE_DIR": str(capture),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.run(
        [PY, str(verifier_root / "run.py"), *test_paths],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(work),
    )
    return proc.returncode, capture, proc.stdout + proc.stderr


def observe(failure_class: str, capture: Path) -> list[str]:
    sys.path[:0] = [str(_runner_root(Path(sys.argv[1])))]
    from challenge_runner.observers import get_observer

    events = list(get_observer(failure_class).observe(str(capture)))
    return [f"{e.observer}/{e.kind}" for e in events]


def main() -> int:
    entry_dir = Path(sys.argv[1]).resolve()
    attack = Path(sys.argv[2]).resolve()
    failure_class = sys.argv[3]
    commits = dict(pair.split("=", 1) for pair in sys.argv[4:])

    verifier_root = build_verifier_root(entry_dir)
    print(f"entry {json.loads((entry_dir / 'entry.json').read_text())['id']}")
    for label, commit_dir in commits.items():
        work = Path(commit_dir).resolve()
        attack_dst = work / "challenge_tests" / attack.name
        attack_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(attack, attack_dst)
        stages = {
            "positive": [str(verifier_root / "controls" / "test_positive.py")],
            "negative": [str(verifier_root / "controls" / "test_negative.py")],
            "attack": [f"challenge_tests/{attack.name}"],
        }
        print(f"\n=== {label} ({work.name}) ===")
        for stage, paths in stages.items():
            code, capture, output = run_stage(verifier_root, work, paths)
            fired = observe(failure_class, capture)
            tail = output.strip().splitlines()[-1] if output.strip() else ""
            print(f"  {stage:9} exit={code} fired={fired or '-'}  {tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
