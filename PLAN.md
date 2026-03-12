# PennGrader 2 Build Plan

## 1) Scope and MVP Goal

Build a single-node PennGrader 2 service using FastAPI + PostgreSQL that supports:

- Student submissions from notebooks.
- Real-time grading progress over SSE with reconnect/replay.
- TA-managed grader scripts per assignment problem.
- Container-isolated grader execution with bounded concurrency.
- Instructor API for aggregate assignment scores.

MVP priorities are correctness, durability across restarts, and clear failure reporting.

## 2) Key Design Decisions

1. Queue in PostgreSQL
- We will use a durable DB-backed queue (`submissions.status=queued/running/...`) with `FOR UPDATE SKIP LOCKED`.
- This preserves queued/running work metadata across API/worker restarts.

2. SSE with persisted event log
- Every user-visible status/progress update is persisted in `submission_events`.
- SSE endpoint replays events after `Last-Event-ID`, then tails new rows.
- Reconnect works even if client or server restarts.

3. Append-only attempts + best-attempt scoring
- Every accepted submit creates a new `submissions` row.
- Aggregate assignment score is `SUM(MAX(score per problem))` for the student.
- Revisions to grader code do not trigger regrading; historical submission scores
  remain valid, so a later stricter grader cannot reduce a previously earned max.

4. Fixed worker concurrency
- Worker runs max `WORKER_CONCURRENCY=5` submissions in parallel (configurable).
- Excess submissions stay queued and receive queue-position updates.

5. Containerized graders with fast startup
- Pre-build a grader runtime image once (not per submission).
- Each grading run launches a short-lived container from that image.
- No retry on crash/timeout; mark submission failed with traceback/error detail.

6. Allowlisted imports/packages
- Grader code imports are validated against an allowlist config.
- Allowlist is centralized config so updates do not require code changes.
- Runtime image installs allowlisted packages to keep per-run startup fast.

7. Static role API keys
- Three static keys (`student`, `ta`, `instructor`) in environment config.
- Role check is enforced at request middleware/dependency layer.

## 3) Data Model (PostgreSQL Schema)

Use migrations (plain SQL files) under `migrations/`.

```sql
CREATE TYPE submission_status AS ENUM (
  'queued',
  'running',
  'succeeded',
  'failed'
);

CREATE TABLE assignments (
  id BIGSERIAL PRIMARY KEY,
  assignment_key TEXT NOT NULL UNIQUE,          -- e.g. "hw1"
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE problems (
  id BIGSERIAL PRIMARY KEY,
  assignment_id BIGINT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  problem_key TEXT NOT NULL,                    -- e.g. "problem1"
  total_points NUMERIC(10,4) NOT NULL CHECK (total_points >= 0),
  grader_source_code TEXT NOT NULL,
  grader_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (assignment_id, problem_key)
);

CREATE TABLE submissions (
  id UUID PRIMARY KEY,
  student_id BIGINT NOT NULL,
  assignment_id BIGINT NOT NULL REFERENCES assignments(id) ON DELETE RESTRICT,
  problem_id BIGINT NOT NULL REFERENCES problems(id) ON DELETE RESTRICT,
  submission_payload JSONB NOT NULL,            -- submitted value/content
  status submission_status NOT NULL,
  queue_entered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ NULL,
  finished_at TIMESTAMPTZ NULL,
  score NUMERIC(10,4) NULL,
  feedback TEXT NULL,
  error_type TEXT NULL,                         -- timeout, container_crash, worker_crash, etc.
  error_traceback TEXT NULL,
  grader_source_hash TEXT NULL,                 -- source hash used at run time (for diagnostics)
  worker_id TEXT NULL
);

CREATE INDEX idx_submissions_queue ON submissions (status, queue_entered_at, id);
CREATE INDEX idx_submissions_student_problem ON submissions (student_id, problem_id, queue_entered_at DESC);
CREATE INDEX idx_submissions_student_assignment ON submissions (student_id, assignment_id);

CREATE TABLE submission_events (
  id BIGSERIAL PRIMARY KEY,                     -- global event id (used as SSE id)
  submission_id UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,                     -- queued, queue_update, started, progress, succeeded, failed
  message TEXT NOT NULL DEFAULT '',
  event_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_submission_id_id ON submission_events (submission_id, id);
```

Derived scoring query for instructor API:

```sql
WITH per_problem_best AS (
  SELECT
    s.student_id,
    s.assignment_id,
    s.problem_id,
    MAX(s.score) AS best_score
  FROM submissions s
  WHERE s.status = 'succeeded'
  GROUP BY s.student_id, s.assignment_id, s.problem_id
)
SELECT
  student_id,
  assignment_id,
  COALESCE(SUM(best_score), 0) AS assignment_score
FROM per_problem_best
GROUP BY student_id, assignment_id;
```

## 4) API Contract (MVP)

### Student key endpoints

1. `POST /v1/student/submissions`
- Body: `student_id`, `assignment_key`, `problem_key`, `submission_payload`.
- Behavior:
  - Enforce rate limit (default 30s between accepted submissions per student).
  - Insert queued submission for the problem's current grader.
  - Insert initial `queued` event with queue position.
- Response: `202 Accepted` with `submission_id`, `events_url`.

2. `GET /v1/student/submissions/{submission_id}`
- Returns current submission status summary (status, score, feedback, errors).

3. `GET /v1/student/submissions/{submission_id}/events` (SSE)
- Supports `Last-Event-ID` replay.
- Streams backlog then live events until terminal state.

4. `GET /v1/student/assignments/{assignment_key}/score?student_id=...`
- Returns aggregate score based on best attempt per problem.

### TA key endpoints

1. `PUT /v1/ta/assignments/{assignment_key}/problems/{problem_key}/grader`
- Body: `source_code`, `total_points`.
- Validates syntax, required grader signature, and import allowlist.
- Updates the problem's current grader source and `total_points`.

2. `GET /v1/ta/assignments/{assignment_key}/problems/{problem_key}/grader`
- Returns the current grader definition for the problem.

### Instructor key endpoints

1. `GET /v1/instructor/assignments/{assignment_key}/students/{student_id}/score`
- Returns aggregate assignment score only.

## 5) Queue + Worker Execution Model

1. Job claim
- Worker loop claims up to `N` queued submissions using `FOR UPDATE SKIP LOCKED`.
- Transition to `running`, set `started_at`, emit `started` event.

2. Queue updates
- API writes initial queue position at enqueue.
- Background queue notifier periodically emits `queue_update` for queued items
  (position calculated by queued count ahead by `queue_entered_at,id`).

3. Grader run
- Worker launches grader container with:
  - timeout: 10 minutes.
  - memory/cpu limits.
  - mounted run payload (submission + latest grader source + metadata).
- Container emits JSONL events on stdout:
  - `progress` messages from callback.
  - final `result` with `score` and `feedback`.
  - on exception, traceback payload.

4. Completion/failure
- On result: set `succeeded`, store score/feedback, emit terminal event.
- On timeout/crash/runner error: set `failed`, store error details, emit terminal event.
- No automatic retry.

5. Restart behavior
- On worker startup, mark any stale `running` submissions as failed with `worker_crash`.
- Emit failure events so reconnecting clients receive final state.

## 6) Grader Runtime Design

1. Runtime image
- Build `penngrader2-grader-runtime` image during deploy.
- Includes Python + grader harness + allowlisted packages.

2. Harness protocol
- Harness imports TA grader source, locates target function, injects callback.
- Writes structured JSONL to stdout for worker to persist as events.

3. Allowlist enforcement
- Parse grader source imports via AST.
- Reject updates that import disallowed modules.
- Allowlist stored in config file (e.g. `config/allowed_imports.toml`), loaded by TA API validation and referenced by runtime image build.

## 7) Notebook Client (`penngrader2`)

Functions:

1. `login(student_id: int)` stores student ID in-process.
2. `submit(assignment_key: str, problem_key: str, value: Any)`:
- POST submission.
- Open SSE stream on `events_url`.
- Track latest SSE id.
- On disconnect, reconnect with exponential backoff and `Last-Event-ID`.
- Render queue/progress updates and return final result object.

## 8) Project Structure (Proposed)

```text
penngrader2/
  app/
    api/
      main.py
      auth.py
      routes_student.py
      routes_ta.py
      routes_instructor.py
    db/
      models.py
      queries.py
      migrations/
    worker/
      queue.py
      runner.py
      queue_notifier.py
    grader_runtime/
      harness.py
      protocol.py
    core/
      config.py
      logging.py
  client/
    penngrader2/__init__.py
    api_client.py
    sse.py
  docker/
    Dockerfile.api
    Dockerfile.grader-runtime
```

## 9) Testing Strategy

1. Unit tests
- Auth role enforcement.
- Rate limit checks.
- Score aggregation query (best-per-problem).
- Grader source validation (signature/import allowlist).

2. Integration tests (Postgres + API + worker)
- Submit -> queued -> running -> succeeded flow with SSE replay.
- Worker timeout path -> failed with traceback/error_type.
- Worker crash simulation -> running submissions marked failed after restart.
- Concurrency cap: only 5 running simultaneously, others queued.

3. Client tests
- SSE reconnect with `Last-Event-ID`.
- Proper final result parsing from terminal event.

## 10) Build Sequence

1. Bootstrap repo with `uv`, FastAPI app, config, logging.
2. Add SQL migrations and DB access layer.
3. Implement role auth + student/TA/instructor endpoints.
4. Implement submission enqueue + rate limiting + score query.
5. Implement worker queue claim loop with concurrency cap.
6. Implement grader runtime harness + container execution integration.
7. Implement SSE event replay/tail endpoint and queue notifier.
8. Implement notebook client (`login`, `submit`, reconnect logic).
9. Add tests (unit + integration) and CI command set.
10. Add Dockerfiles and local run docs (API + worker + Postgres).

## 11) Config Surface (Initial)

- `PG_DSN`
- `API_KEY_STUDENT`
- `API_KEY_TA`
- `API_KEY_INSTRUCTOR`
- `WORKER_CONCURRENCY` (default `5`)
- `SUBMISSION_RATE_LIMIT_SECONDS` (default `30`)
- `GRADER_TIMEOUT_SECONDS` (default `600`)
- `GRADER_RUNTIME_IMAGE`
- `ALLOWED_IMPORTS_FILE`

## 12) Open Choices to Confirm Before Build

1. `submission_payload` size limits and accepted content types (JSON/text/binary).
2. Whether TA endpoint should return raw grader source in GET by default.
3. Event retention policy for `submission_events` (indefinite vs TTL cleanup job).
