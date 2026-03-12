from __future__ import annotations

from decimal import Decimal

import pytest

from penngrader2.client import PennGraderClient, PennGraderClientError


class DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text or f"HTTP {self.status_code}")


class DummyStreamResponse:
    def __init__(self, status_code: int, lines: list[str], text: str = "") -> None:
        self.status_code = status_code
        self._lines = lines
        self.text = text

    def __enter__(self) -> "DummyStreamResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def iter_lines(self, decode_unicode: bool = True):
        return iter(self._lines)


def test_upload_grader_sends_ta_request(monkeypatch):
    captured: dict[str, object] = {}

    def fake_put(url, *, headers, timeout, json):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["json"] = json
        return DummyResponse(
            200,
            payload={
                "assignment_key": "hw1",
                "problem_key": "problem1",
                "total_points": "5",
                "grader_source_code": "def grade_problem1(submission, callback):\n    return (5, 'Correct')\n",
                "grader_updated_at": "2026-03-12T00:00:00Z",
            },
        )

    monkeypatch.setattr("penngrader2.client.requests.put", fake_put)

    client = PennGraderClient(base_url="https://grader.example", api_key="ta-key", timeout_seconds=17)
    result = client.upload_grader(
        assignment_key="hw1",
        problem_key="problem1",
        total_points=Decimal("5"),
        source_code="def grade_problem1(submission, callback):\n    return (5, 'Correct')\n",
    )

    assert captured["url"] == "https://grader.example/v1/ta/assignments/hw1/problems/problem1/grader"
    assert captured["headers"] == {"X-API-Key": "ta-key"}
    assert captured["timeout"] == 17
    assert captured["json"] == {
        "total_points": "5",
        "source_code": "def grade_problem1(submission, callback):\n    return (5, 'Correct')\n",
    }
    assert result["problem_key"] == "problem1"


def test_upload_grader_requires_ta_key_on_auth_failure(monkeypatch):
    def fake_put(url, *, headers, timeout, json):
        return DummyResponse(403, text="forbidden")

    monkeypatch.setattr("penngrader2.client.requests.put", fake_put)

    client = PennGraderClient(base_url="https://grader.example", api_key="student-key")

    with pytest.raises(PennGraderClientError, match="TA API key required"):
        client.upload_grader(
            assignment_key="hw1",
            problem_key="problem1",
            total_points=5,
            source_code="def grade_problem1(submission, callback):\n    return (5, 'Correct')\n",
        )


def test_submit_prints_full_credit_summary(monkeypatch, capsys):
    def fake_post(url, *, headers, timeout, json):
        return DummyResponse(
            202,
            payload={
                "submission_id": "sub-1",
                "events_url": "https://grader.example/events/sub-1",
                "queue_position": 1,
            },
        )

    def fake_get(url, *, headers, timeout, stream=False, params=None):
        if stream:
            return DummyStreamResponse(
                200,
                [
                    "id: 1",
                    "event: queued",
                    'data: {"message":"Queued for grading","payload":{"queue_position":1},"created_at":"2026-03-12T00:00:00Z"}',
                    "",
                    "id: 2",
                    "event: started",
                    'data: {"message":"Grading started","payload":{},"created_at":"2026-03-12T00:00:01Z"}',
                    "",
                    "id: 3",
                    "event: succeeded",
                    'data: {"message":"","payload":{"score":5,"feedback":"Correct"},"created_at":"2026-03-12T00:00:02Z"}',
                    "",
                ],
            )
        return DummyResponse(
            200,
            payload={
                "submission_id": "sub-1",
                "student_id": 21837184,
                "assignment_key": "hw1",
                "problem_key": "problem1",
                "status": "succeeded",
                "total_points": "5",
                "score": "5",
                "feedback": "Correct",
                "error_type": None,
                "error_traceback": None,
            },
        )

    monkeypatch.setattr("penngrader2.client.requests.post", fake_post)
    monkeypatch.setattr("penngrader2.client.requests.get", fake_get)

    client = PennGraderClient(base_url="https://grader.example", api_key="student-key")
    client.login(21837184)

    result = client.submit("hw1", "problem1", 42)
    captured = capsys.readouterr().out

    assert result is None
    assert "[queued] Queued for grading" in captured
    assert "[started] Grading started" in captured
    assert "✅ Correct. Score: 5/5. Correct" in captured


def test_submit_prints_error_summary(monkeypatch, capsys):
    def fake_post(url, *, headers, timeout, json):
        return DummyResponse(
            202,
            payload={
                "submission_id": "sub-2",
                "events_url": "https://grader.example/events/sub-2",
                "queue_position": 1,
            },
        )

    def fake_get(url, *, headers, timeout, stream=False, params=None):
        if stream:
            return DummyStreamResponse(
                200,
                [
                    "id: 1",
                    "event: queued",
                    'data: {"message":"Queued for grading","payload":{"queue_position":1},"created_at":"2026-03-12T00:00:00Z"}',
                    "",
                    "id: 2",
                    "event: failed",
                    'data: {"message":"","payload":{"error_type":"timeout"},"created_at":"2026-03-12T00:10:00Z"}',
                    "",
                ],
            )
        return DummyResponse(
            200,
            payload={
                "submission_id": "sub-2",
                "student_id": 21837184,
                "assignment_key": "hw1",
                "problem_key": "problem1",
                "status": "failed",
                "total_points": "5",
                "score": None,
                "feedback": "Grader execution timed out",
                "error_type": "timeout",
                "error_traceback": None,
            },
        )

    monkeypatch.setattr("penngrader2.client.requests.post", fake_post)
    monkeypatch.setattr("penngrader2.client.requests.get", fake_get)

    client = PennGraderClient(base_url="https://grader.example", api_key="student-key")
    client.login(21837184)

    result = client.submit("hw1", "problem1", 42)
    captured = capsys.readouterr().out

    assert result is None
    assert "Error while grading. Score: unavailable (max 5). Grader execution timed out" in captured
