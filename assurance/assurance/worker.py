"""Worker process. It can reach the gateway and the board, not the exchange."""

from __future__ import annotations

import os


class WorkerIsolationError(Exception):
    pass


def assert_worker_isolated(env: dict[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    if env.get("EXCHANGE_URL", "").strip():
        raise WorkerIsolationError("a worker must not receive EXCHANGE_URL")
    for key in ("REQUESTER_API_KEY", "OBSERVER_API_KEY", "EXCHANGE_OPERATOR_KEY"):
        if env.get(key, "").strip() or env.get(f"{key}_FILE", "").strip():
            raise WorkerIsolationError(f"a worker must not receive {key}")


def main() -> int:
    assert_worker_isolated()
    print("worker isolated; waiting for a gateway tool call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
