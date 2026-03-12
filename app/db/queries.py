from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb


TERMINAL_STATUSES = {"succeeded", "failed"}


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after_seconds: float


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def check_rate_limit(conn: Connection, student_id: int, min_seconds: int) -> RateLimitResult:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT queue_entered_at
            FROM submissions
            WHERE student_id = %s
            ORDER BY queue_entered_at DESC
            LIMIT 1
            """,
            (student_id,),
        )
        row = cur.fetchone()

    if not row:
        return RateLimitResult(allowed=True, retry_after_seconds=0)

    seconds_since = (_now_utc() - row["queue_entered_at"]).total_seconds()
    retry = max(0.0, float(min_seconds - seconds_since))
    return RateLimitResult(allowed=retry <= 0, retry_after_seconds=retry)


def upsert_problem_grader(
    conn: Connection,
    assignment_key: str,
    problem_key: str,
    total_points: Decimal,
    source_code: str,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO assignments (assignment_key)
            VALUES (%s)
            ON CONFLICT (assignment_key) DO UPDATE SET assignment_key = EXCLUDED.assignment_key
            RETURNING id, assignment_key
            """,
            (assignment_key,),
        )
        assignment = cur.fetchone()

        cur.execute(
            """
            INSERT INTO problems (assignment_id, problem_key, total_points, grader_source_code)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (assignment_id, problem_key)
            DO UPDATE SET
              total_points = EXCLUDED.total_points,
              grader_source_code = EXCLUDED.grader_source_code,
              grader_updated_at = now()
            RETURNING id, assignment_id, problem_key, total_points, grader_updated_at
            """,
            (assignment["id"], problem_key, total_points, source_code),
        )
        problem = cur.fetchone()

    return {
        "assignment_id": assignment["id"],
        "assignment_key": assignment["assignment_key"],
        "problem_id": problem["id"],
        "problem_key": problem["problem_key"],
        "total_points": str(problem["total_points"]),
        "grader_updated_at": problem["grader_updated_at"].isoformat(),
    }


def get_problem(conn: Connection, assignment_key: str, problem_key: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              a.id AS assignment_id,
              a.assignment_key,
              p.id AS problem_id,
              p.problem_key,
              p.total_points,
              p.grader_source_code,
              p.grader_updated_at
            FROM assignments a
            JOIN problems p ON p.assignment_id = a.id
            WHERE a.assignment_key = %s AND p.problem_key = %s
            """,
            (assignment_key, problem_key),
        )
        return cur.fetchone()


def create_submission(
    conn: Connection,
    student_id: int,
    assignment_id: int,
    problem_id: int,
    submission_payload: Any,
    grader_source_code: str,
) -> dict[str, Any]:
    submission_id = uuid.uuid4()
    source_hash = hashlib.sha256(grader_source_code.encode("utf-8")).hexdigest()

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO submissions (
              id,
              student_id,
              assignment_id,
              problem_id,
              submission_payload,
              status,
              grader_source_hash
            )
            VALUES (%s, %s, %s, %s, %s, 'queued', %s)
            RETURNING id, queue_entered_at
            """,
            (submission_id, student_id, assignment_id, problem_id, Jsonb(submission_payload), source_hash),
        )
        inserted = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(*)::BIGINT AS ahead
            FROM submissions
            WHERE status = 'queued'
              AND (
                queue_entered_at < %s OR
                (queue_entered_at = %s AND id < %s)
              )
            """,
            (inserted["queue_entered_at"], inserted["queue_entered_at"], inserted["id"]),
        )
        ahead = cur.fetchone()["ahead"]
        position = int(ahead) + 1

    insert_submission_event(
        conn,
        submission_id,
        "queued",
        f"Queued for grading (position {position})",
        {"queue_position": position},
    )

    return {"submission_id": str(submission_id), "queue_position": position}


def insert_submission_event(
    conn: Connection,
    submission_id: uuid.UUID | str,
    event_type: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO submission_events (submission_id, event_type, message, event_payload)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (submission_id, event_type, message, Jsonb(payload or {})),
        )
        row = cur.fetchone()
    return int(row["id"])


def get_submission(conn: Connection, submission_id: uuid.UUID | str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              s.id,
              s.student_id,
              a.assignment_key,
              p.problem_key,
              p.total_points,
              s.status,
              s.score,
              s.feedback,
              s.error_type,
              s.error_traceback,
              s.queue_entered_at,
              s.started_at,
              s.finished_at
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            JOIN problems p ON p.id = s.problem_id
            WHERE s.id = %s
            """,
            (submission_id,),
        )
        return cur.fetchone()


def get_submission_for_student(conn: Connection, submission_id: uuid.UUID | str, student_id: int) -> dict[str, Any] | None:
    row = get_submission(conn, submission_id)
    if not row:
        return None
    if row["student_id"] != student_id:
        return None
    return row


def list_submission_events(
    conn: Connection,
    submission_id: uuid.UUID | str,
    after_event_id: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, event_type, message, event_payload, created_at
            FROM submission_events
            WHERE submission_id = %s
              AND id > %s
            ORDER BY id ASC
            LIMIT %s
            """,
            (submission_id, after_event_id, limit),
        )
        return list(cur.fetchall())


def get_assignment_score(conn: Connection, student_id: int, assignment_key: str) -> Decimal:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH per_problem_best AS (
              SELECT
                s.student_id,
                s.assignment_id,
                s.problem_id,
                MAX(s.score) AS best_score
              FROM submissions s
              WHERE s.status = 'succeeded' AND s.student_id = %s
              GROUP BY s.student_id, s.assignment_id, s.problem_id
            )
            SELECT COALESCE(SUM(ppb.best_score), 0) AS assignment_score
            FROM per_problem_best ppb
            JOIN assignments a ON a.id = ppb.assignment_id
            WHERE a.assignment_key = %s
            """,
            (student_id, assignment_key),
        )
        row = cur.fetchone()
    return row["assignment_score"]


def claim_queued_submissions(conn: Connection, limit: int, worker_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH claimed AS (
              SELECT id
              FROM submissions
              WHERE status = 'queued'
              ORDER BY queue_entered_at, id
              FOR UPDATE SKIP LOCKED
              LIMIT %s
            )
            UPDATE submissions s
            SET status = 'running', started_at = now(), worker_id = %s
            FROM claimed c, assignments a, problems p
            WHERE s.id = c.id
              AND a.id = s.assignment_id
              AND p.id = s.problem_id
            RETURNING
              s.id,
              s.student_id,
              s.submission_payload,
              a.assignment_key,
              p.problem_key,
              p.total_points,
              p.grader_source_code
            """,
            (limit, worker_id),
        )
        return list(cur.fetchall())


def mark_submission_started(conn: Connection, submission_id: uuid.UUID | str) -> None:
    insert_submission_event(conn, submission_id, "started", "Grading started", {})


def mark_submission_succeeded(
    conn: Connection,
    submission_id: uuid.UUID | str,
    score: Decimal,
    feedback: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE submissions
            SET status = 'succeeded', score = %s, feedback = %s, finished_at = now()
            WHERE id = %s
            """,
            (score, feedback, submission_id),
        )
    insert_submission_event(
        conn,
        submission_id,
        "succeeded",
        "Grading completed",
        {"score": float(score), "feedback": feedback},
    )


def mark_submission_failed(
    conn: Connection,
    submission_id: uuid.UUID | str,
    error_type: str,
    error_traceback: str,
    message: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE submissions
            SET status = 'failed', error_type = %s, error_traceback = %s, finished_at = now(), feedback = %s
            WHERE id = %s
            """,
            (error_type, error_traceback, message, submission_id),
        )
    insert_submission_event(
        conn,
        submission_id,
        "failed",
        message,
        {"error_type": error_type, "traceback": error_traceback},
    )


def mark_stale_running_submissions_failed(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE submissions
            SET
              status = 'failed',
              error_type = 'worker_crash',
              error_traceback = 'Worker restarted while submission was running',
              feedback = 'Worker crashed/restarted during grading',
              finished_at = now()
            WHERE status = 'running'
            RETURNING id
            """
        )
        rows = list(cur.fetchall())

    for row in rows:
        insert_submission_event(
            conn,
            row["id"],
            "failed",
            "Worker restarted while grading was in progress",
            {"error_type": "worker_crash"},
        )

    return len(rows)


def emit_queue_position_events(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH ranked AS (
              SELECT
                id,
                ROW_NUMBER() OVER (ORDER BY queue_entered_at, id) AS pos
              FROM submissions
              WHERE status = 'queued'
            )
            SELECT id, pos
            FROM ranked
            ORDER BY pos ASC
            """
        )
        rows = list(cur.fetchall())

    count = 0
    for row in rows:
        insert_submission_event(
            conn,
            row["id"],
            "queue_update",
            f"Waiting in queue (position {row['pos']})",
            {"queue_position": int(row["pos"])},
        )
        count += 1
    return count
