from __future__ import annotations

import json
import signal
import sys
import traceback
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


class TimeoutErrorInHarness(Exception):
    pass


def _timeout_handler(_signum: int, _frame: Any) -> None:
    raise TimeoutErrorInHarness("Grader function timed out")


def main() -> int:
    if len(sys.argv) != 2:
        emit({"type": "error", "error_type": "protocol_error", "message": "Expected payload path"})
        return 2

    payload_arg = sys.argv[1]
    if payload_arg == "-":
        payload = json.load(sys.stdin)
    else:
        payload_path = Path(payload_arg)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))

    source_code = payload["source_code"]
    function_name = payload["function_name"]
    submission = payload["submission"]
    timeout_seconds = int(payload.get("timeout_seconds", 600))

    namespace: dict[str, Any] = {}
    try:
        exec(compile(source_code, "grader_source", "exec"), namespace, namespace)
        grade_fn = namespace.get(function_name)
        if grade_fn is None or not callable(grade_fn):
            emit(
                {
                    "type": "error",
                    "error_type": "missing_function",
                    "message": f"Function {function_name} not found",
                    "traceback": "",
                }
            )
            return 1

        def callback(message: str) -> None:
            emit({"type": "progress", "message": str(message), "payload": {}})

        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_seconds)
        result = grade_fn(submission, callback)
        signal.alarm(0)

        if not isinstance(result, (tuple, list)) or len(result) != 2:
            emit(
                {
                    "type": "error",
                    "error_type": "bad_return_value",
                    "message": "Grader must return (score, feedback)",
                    "traceback": "",
                }
            )
            return 1

        score, feedback = result
        emit({"type": "result", "score": score, "feedback": str(feedback)})
        return 0

    except TimeoutErrorInHarness as exc:
        emit(
            {
                "type": "error",
                "error_type": "timeout",
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        emit(
            {
                "type": "error",
                "error_type": "grader_exception",
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
