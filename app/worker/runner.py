from __future__ import annotations

import json
import select
import subprocess
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable


@dataclass
class RunnerResult:
    ok: bool
    score: Decimal | None
    feedback: str
    error_type: str | None
    error_traceback: str | None


def run_grader_container(
    *,
    runtime_image: str,
    harness_path_in_container: str,
    timeout_seconds: int,
    memory_limit: str,
    cpus: str,
    source_code: str,
    function_name: str,
    submission_payload: Any,
    total_points: Decimal,
    on_progress: Callable[[str, dict[str, Any]], None],
) -> RunnerResult:
    payload = {
        "source_code": source_code,
        "function_name": function_name,
        "submission": submission_payload,
        "total_points": float(total_points),
        "timeout_seconds": timeout_seconds,
    }

    with tempfile.TemporaryDirectory(prefix="penngrader2-run-") as tmpdir:
        payload_path = Path(tmpdir) / "payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")

        cmd = [
            "docker",
            "run",
            "--rm",
            "-m",
            memory_limit,
            "--cpus",
            cpus,
            "-v",
            f"{payload_path}:/workspace/payload.json:ro",
            runtime_image,
            "python",
            harness_path_in_container,
            "/workspace/payload.json",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        assert proc.stdout is not None
        assert proc.stderr is not None

        start = time.monotonic()
        result_score: Decimal | None = None
        result_feedback = ""
        captured_error_type: str | None = None
        captured_traceback: str | None = None

        def handle_output_line(line: str) -> None:
            nonlocal result_score
            nonlocal result_feedback
            nonlocal captured_error_type
            nonlocal captured_traceback

            line = line.strip()
            if not line:
                return

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                return

            msg_type = msg.get("type")
            if msg_type == "progress":
                message = str(msg.get("message", ""))
                payload_obj = msg.get("payload", {})
                if not isinstance(payload_obj, dict):
                    payload_obj = {"raw": payload_obj}
                on_progress(message, payload_obj)
            elif msg_type == "result":
                raw_score = msg.get("score", 0)
                result_score = Decimal(str(raw_score))
                result_feedback = str(msg.get("feedback", ""))
            elif msg_type == "error":
                captured_error_type = str(msg.get("error_type", "grader_error"))
                captured_traceback = str(msg.get("traceback", ""))
                result_feedback = str(msg.get("message", "Grader failed"))

        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout_seconds:
                proc.kill()
                return RunnerResult(
                    ok=False,
                    score=None,
                    feedback="Grader execution timed out",
                    error_type="timeout",
                    error_traceback="Timed out while waiting for grader container",
                )

            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if ready:
                line = proc.stdout.readline()
                if line:
                    handle_output_line(line)

            if proc.poll() is not None:
                for line in proc.stdout.readlines():
                    handle_output_line(line)
                break

        stderr = proc.stderr.read().strip()
        return_code = proc.wait()

    if captured_error_type:
        return RunnerResult(
            ok=False,
            score=None,
            feedback=result_feedback or "Grader failed",
            error_type=captured_error_type,
            error_traceback=captured_traceback or stderr,
        )

    if return_code != 0:
        return RunnerResult(
            ok=False,
            score=None,
            feedback="Grader container exited with non-zero status",
            error_type="container_error",
            error_traceback=stderr,
        )

    if result_score is None:
        return RunnerResult(
            ok=False,
            score=None,
            feedback="Grader did not return a result",
            error_type="protocol_error",
            error_traceback=stderr,
        )

    return RunnerResult(
        ok=True,
        score=result_score,
        feedback=result_feedback,
        error_type=None,
        error_traceback=None,
    )
