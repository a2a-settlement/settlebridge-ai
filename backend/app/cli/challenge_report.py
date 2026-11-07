"""Sign and verify a challenge evidence report.

Run from the backend directory::

    python -m app.cli.challenge_report sign report.json --key-file private.pem
    python -m app.cli.challenge_report verify report.jws --public-key-file public.pem

Keys are read from the named files. The commands never print key material.
``sign`` writes a compact JWS to stdout. ``verify`` prints ``ok`` when the
signature, algorithm, and payload check out.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.schemas.challenge import ChallengeReport
from app.services.challenge_report import sign_report, verify_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli.challenge_report")
    sub = parser.add_subparsers(dest="command", required=True)

    sign = sub.add_parser("sign", help="Sign a ChallengeReport JSON file")
    sign.add_argument("report_json", metavar="report.json")
    sign.add_argument("--key-file", required=True)

    verify = sub.add_parser("verify", help="Verify a compact challenge-report JWS")
    verify.add_argument("jws_file", metavar="file.jws")
    verify.add_argument("--public-key-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sign":
        return _sign(args.report_json, args.key_file)
    return _verify(args.jws_file, args.public_key_file)


def _sign(report_path: str, key_path: str) -> int:
    try:
        report = ChallengeReport.model_validate_json(_read(report_path))
        token = sign_report(report, _read(key_path))
    except Exception:
        sys.stderr.write("sign failed\n")
        return 1
    sys.stdout.write(token + "\n")
    return 0


def _verify(jws_path: str, public_key_path: str) -> int:
    try:
        verify_report(_read(jws_path).strip(), _read(public_key_path))
    except Exception:
        sys.stderr.write("verify failed\n")
        return 1
    sys.stdout.write("ok\n")
    return 0


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
