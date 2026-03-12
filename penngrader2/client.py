from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests


class PennGraderClientError(RuntimeError):
    pass


@dataclass
class SSEEvent:
    id: int | None
    event: str
    data: dict[str, Any]


class PennGraderClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.student_id: int | None = None

    def login(self, student_id: int) -> None:
        self.student_id = int(student_id)

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    def submit(self, assignment_key: str, problem_key: str, submission_payload: Any) -> None:
        if self.student_id is None:
            raise PennGraderClientError("Call login(student_id) before submit()")

        headers = self._headers()
        response = requests.post(
            f"{self.base_url}/v1/student/submissions",
            headers=headers,
            timeout=self.timeout_seconds,
            json={
                "student_id": self.student_id,
                "assignment_key": assignment_key,
                "problem_key": problem_key,
                "submission_payload": submission_payload,
            },
        )
        if response.status_code >= 400:
            raise PennGraderClientError(
                f"submit failed ({response.status_code}): {response.text}"
            )

        body = response.json()
        events_url = body["events_url"]
        last_event_id = 0
        backoff = 1.0
        final_data: dict[str, Any] | None = None

        while True:
            stream_headers = dict(headers)
            if last_event_id:
                stream_headers["Last-Event-ID"] = str(last_event_id)

            try:
                with requests.get(
                    events_url,
                    headers=stream_headers,
                    timeout=self.timeout_seconds,
                    stream=True,
                ) as stream:
                    if stream.status_code >= 400:
                        raise PennGraderClientError(
                            f"event stream failed ({stream.status_code}): {stream.text}"
                        )
                    for event in _iter_sse(stream.iter_lines(decode_unicode=True)):
                        if event.id is not None:
                            last_event_id = event.id
                        message = event.data.get("message")
                        if message:
                            print(f"[{event.event}] {message}")

                        if event.event in {"succeeded", "failed"}:
                            final_data = event.data
                            break

                if final_data is not None:
                    break

            except requests.RequestException:
                time.sleep(backoff)
                backoff = min(backoff * 2, 10)
                continue

        status_resp = requests.get(
            f"{self.base_url}/v1/student/submissions/{body['submission_id']}",
            headers=headers,
            params={"student_id": self.student_id},
            timeout=self.timeout_seconds,
        )
        status_resp.raise_for_status()
        status_body = status_resp.json()
        print(_format_submission_summary(status_body))
        return None

    def upload_grader(
        self,
        assignment_key: str,
        problem_key: str,
        total_points: int | float | str | Decimal,
        source_code: str,
    ) -> dict[str, Any]:
        response = requests.put(
            f"{self.base_url}/v1/ta/assignments/{assignment_key}/problems/{problem_key}/grader",
            headers=self._headers(),
            timeout=self.timeout_seconds,
            json={
                "total_points": str(total_points) if isinstance(total_points, Decimal) else total_points,
                "source_code": source_code,
            },
        )
        if response.status_code in {401, 403}:
            raise PennGraderClientError(
                f"upload_grader failed ({response.status_code}): TA API key required: {response.text}"
            )
        if response.status_code >= 400:
            raise PennGraderClientError(
                f"upload_grader failed ({response.status_code}): {response.text}"
            )
        return response.json()


def _iter_sse(lines):
    event_id: int | None = None
    event_name = "message"
    data_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if data_lines:
                try:
                    payload = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    payload = {"raw": "\n".join(data_lines)}
                yield SSEEvent(id=event_id, event=event_name, data=payload)
            event_id = None
            event_name = "message"
            data_lines = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("id:"):
            value = line[3:].strip()
            try:
                event_id = int(value)
            except ValueError:
                event_id = None
        elif line.startswith("event:"):
            event_name = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())


def _format_submission_summary(status_body: dict[str, Any]) -> str:
    status = str(status_body.get("status", "unknown"))
    score = status_body.get("score")
    total_points = status_body.get("total_points")
    feedback = status_body.get("feedback")
    error_type = status_body.get("error_type")

    if status == "failed":
        prefix = "Error while grading."
    elif score is not None and total_points is not None and _is_full_credit(score, total_points):
        prefix = "✅ Correct."
    else:
        prefix = "Incorrect."

    parts = [prefix, _format_score_text(score, total_points)]
    if feedback:
        parts.append(str(feedback))
    elif error_type:
        parts.append(f"Error type: {error_type}.")
    return " ".join(parts)


def _format_score_text(score: Any, total_points: Any) -> str:
    if score is None and total_points is None:
        return "Score: unavailable."
    if score is None:
        return f"Score: unavailable (max {_display_number(total_points)})."
    if total_points is None:
        return f"Score: {_display_number(score)}."
    return f"Score: {_display_number(score)}/{_display_number(total_points)}."


def _is_full_credit(score: Any, total_points: Any) -> bool:
    return Decimal(str(score)) == Decimal(str(total_points))


def _display_number(value: Any) -> str:
    decimal_value = Decimal(str(value))
    if decimal_value == decimal_value.to_integral():
        return str(decimal_value.quantize(Decimal("1")))
    return format(decimal_value.normalize(), "f")


_client: PennGraderClient | None = None


def configure(base_url: str, api_key: str) -> None:
    global _client
    _client = PennGraderClient(base_url=base_url, api_key=api_key)


def login(student_id: int) -> None:
    if _client is None:
        raise RuntimeError("Call penngrader2.configure(base_url, api_key) first")
    _client.login(student_id)


def submit(assignment_key: str, problem_key: str, submission_payload: Any) -> None:
    if _client is None:
        raise RuntimeError("Call penngrader2.configure(base_url, api_key) first")
    return _client.submit(assignment_key, problem_key, submission_payload)


def upload_grader(
    assignment_key: str,
    problem_key: str,
    total_points: int | float | str | Decimal,
    source_code: str,
) -> dict[str, Any]:
    if _client is None:
        raise RuntimeError("Call penngrader2.configure(base_url, api_key) first")
    return _client.upload_grader(
        assignment_key=assignment_key,
        problem_key=problem_key,
        total_points=total_points,
        source_code=source_code,
    )
