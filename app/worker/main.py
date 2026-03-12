from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from decimal import Decimal
from typing import Any

from app.core.config import get_settings
from app.core.grader_validation import expected_grader_function_name
from app.core.logging import configure_logging
from app.db.connection import get_conn
from app.db.queries import (
    claim_queued_submissions,
    emit_queue_position_events,
    insert_submission_event,
    mark_stale_running_submissions_failed,
    mark_submission_failed,
    mark_submission_started,
    mark_submission_succeeded,
)
from app.worker.runner import run_grader_container

logger = logging.getLogger(__name__)


def _process_submission(job: dict[str, Any]) -> None:
    settings = get_settings()
    submission_id = job["id"]
    fn_name = expected_grader_function_name(job["problem_key"])

    with get_conn() as conn:
        total_points = Decimal(str(job["total_points"]))

        def on_progress(message: str, payload: dict[str, Any]) -> None:
            insert_submission_event(conn, submission_id, "progress", message, payload)
            conn.commit()

        result = run_grader_container(
            runtime_image=settings.grader_runtime_image,
            harness_path_in_container="/opt/penngrader/harness.py",
            timeout_seconds=settings.grader_timeout_seconds,
            memory_limit=settings.grader_memory_limit,
            cpus=settings.grader_cpus,
            source_code=job["grader_source_code"],
            function_name=fn_name,
            submission_payload=job["submission_payload"],
            total_points=total_points,
            on_progress=on_progress,
        )

        if result.ok:
            bounded_score = max(Decimal("0"), min(result.score or Decimal("0"), total_points))
            mark_submission_succeeded(conn, submission_id, bounded_score, result.feedback)
            conn.commit()
            logger.info("Submission %s succeeded with score %s", submission_id, bounded_score)
            return

        mark_submission_failed(
            conn,
            submission_id,
            result.error_type or "worker_error",
            result.error_traceback or "",
            result.feedback,
        )
        conn.commit()
        logger.warning("Submission %s failed: %s", submission_id, result.error_type)


def main() -> None:
    configure_logging()
    settings = get_settings()

    with get_conn() as conn:
        crashed = mark_stale_running_submissions_failed(conn)
        conn.commit()
    if crashed:
        logger.warning("Marked %d stale running submissions as failed", crashed)

    futures: dict[Future[None], str] = {}
    last_queue_update = 0.0

    with ThreadPoolExecutor(max_workers=settings.worker_concurrency) as executor:
        while True:
            made_progress = False
            done = [future for future in futures if future.done()]
            for future in done:
                submission_id = futures.pop(future)
                try:
                    future.result()
                except Exception:
                    logger.exception("Unhandled worker error for submission %s", submission_id)
                made_progress = True

            available_slots = settings.worker_concurrency - len(futures)
            if available_slots > 0:
                with get_conn() as conn:
                    claimed = claim_queued_submissions(conn, available_slots, settings.worker_id)
                    for row in claimed:
                        mark_submission_started(conn, row["id"])
                    conn.commit()

                for job in claimed:
                    future = executor.submit(_process_submission, job)
                    futures[future] = str(job["id"])
                if claimed:
                    made_progress = True

            now = time.monotonic()
            if now - last_queue_update >= settings.queue_update_interval_seconds:
                with get_conn() as conn:
                    emit_queue_position_events(conn)
                    conn.commit()
                last_queue_update = now
                made_progress = True

            if made_progress:
                continue

            if futures:
                wait(
                    set(futures.keys()),
                    timeout=settings.worker_poll_interval_seconds,
                    return_when=FIRST_COMPLETED,
                )
            else:
                time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
