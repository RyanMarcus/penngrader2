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
