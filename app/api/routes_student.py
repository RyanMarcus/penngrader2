from __future__ import annotations

import json
import time
from typing import Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.auth import ROLE_STUDENT, require_role
from app.api.schemas import (
    StudentAssignmentScoreResponse,
    StudentSubmissionCreateRequest,
    StudentSubmissionCreateResponse,
    SubmissionStatusResponse,
)
from app.core.config import Settings, get_settings
from app.db.connection import get_conn
from app.db.queries import (
    TERMINAL_STATUSES,
    check_rate_limit,
    create_submission,
    get_assignment_score,
    get_problem,
    get_submission_for_student,
    list_submission_events,
)

router = APIRouter(prefix="/v1/student", tags=["student"])


@router.post("/submissions", response_model=StudentSubmissionCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_student_submission(
    payload: StudentSubmissionCreateRequest,
    request: Request,
    _: str = Depends(require_role(ROLE_STUDENT)),
    settings: Settings = Depends(get_settings),
):
    with get_conn() as conn:
        rate_limit = check_rate_limit(conn, payload.student_id, settings.submission_rate_limit_seconds)
        if not rate_limit.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limited. Retry in {int(rate_limit.retry_after_seconds) + 1}s",
                headers={"Retry-After": str(int(rate_limit.retry_after_seconds) + 1)},
            )

        problem = get_problem(conn, payload.assignment_key, payload.problem_key)
        if not problem:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown assignment/problem")

        created = create_submission(
            conn,
            student_id=payload.student_id,
            assignment_id=problem["assignment_id"],
            problem_id=problem["problem_id"],
            submission_payload=payload.submission_payload,
            grader_source_code=problem["grader_source_code"],
        )
        conn.commit()

    events_url = str(
        request.url_for("stream_student_submission_events", submission_id=created["submission_id"])
    ) + f"?student_id={payload.student_id}"

    return StudentSubmissionCreateResponse(
        submission_id=created["submission_id"],
        events_url=events_url,
        queue_position=created["queue_position"],
    )


@router.get("/submissions/{submission_id}", response_model=SubmissionStatusResponse)
def get_student_submission(
    submission_id: UUID,
    student_id: int = Query(...),
    _: str = Depends(require_role(ROLE_STUDENT)),
):
    with get_conn() as conn:
        row = get_submission_for_student(conn, submission_id, student_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

    return SubmissionStatusResponse(
        submission_id=row["id"],
        student_id=row["student_id"],
        assignment_key=row["assignment_key"],
        problem_key=row["problem_key"],
        status=row["status"],
        total_points=row["total_points"],
        score=row["score"],
        feedback=row["feedback"],
        error_type=row["error_type"],
        error_traceback=row["error_traceback"],
    )


@router.get(
    "/submissions/{submission_id}/events",
    name="stream_student_submission_events",
)
def stream_student_submission_events(
    submission_id: UUID,
    student_id: int = Query(...),
    last_event_id: int | None = Query(default=None),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
    _: str = Depends(require_role(ROLE_STUDENT)),
    settings: Settings = Depends(get_settings),
):
    with get_conn() as conn:
        row = get_submission_for_student(conn, submission_id, student_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

    start_after = 0
    if last_event_id is not None:
        start_after = last_event_id
    elif last_event_id_header:
        try:
            start_after = int(last_event_id_header)
        except ValueError:
            start_after = 0

    def event_stream() -> Iterator[str]:
        current_id = start_after
        last_heartbeat = time.monotonic()

        while True:
            with get_conn() as conn:
                current = get_submission_for_student(conn, submission_id, student_id)
                events = list_submission_events(conn, submission_id, current_id)

            for event in events:
                current_id = event["id"]
                payload = {
                    "type": event["event_type"],
                    "message": event["message"],
                    "payload": event["event_payload"],
                    "created_at": event["created_at"].isoformat(),
                }
                yield f"id: {event['id']}\n"
                yield f"event: {event['event_type']}\n"
                yield f"data: {json.dumps(payload)}\n\n"
                last_heartbeat = time.monotonic()

            if current and current["status"] in TERMINAL_STATUSES and not events:
                break

            now = time.monotonic()
            if now - last_heartbeat >= settings.event_heartbeat_seconds:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            time.sleep(settings.event_poll_interval_seconds)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.get("/assignments/{assignment_key}/score", response_model=StudentAssignmentScoreResponse)
def get_student_assignment_score(
    assignment_key: str,
    student_id: int = Query(...),
    _: str = Depends(require_role(ROLE_STUDENT)),
):
    with get_conn() as conn:
        score = get_assignment_score(conn, student_id, assignment_key)
    return StudentAssignmentScoreResponse(assignment_key=assignment_key, student_id=student_id, score=score)
