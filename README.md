# PennGrader 2

PennGrader 2 is a single-node grading service for notebook-based assignments. It provides student submissions with SSE progress updates, TA-managed grader scripts, worker-based container execution, and instructor aggregate score APIs.

## Operations

### Service layout

- API: `app/api/main.py`
- Queue, state, and events: PostgreSQL
- Worker: `app/worker/main.py`
- Grader runtime image: `docker/Dockerfile.grader-runtime`
- Default health check: `GET /healthz`

### Prerequisites

- Python 3.11+
- Docker with a running daemon
- PostgreSQL

### Configuration

Copy the sample environment file before running the service:

```bash
cp .env.example .env
```

Important environment variables:

- `PG_DSN`
- `API_KEY_STUDENT`
- `API_KEY_TA`
- `API_KEY_INSTRUCTOR`
- `WORKER_CONCURRENCY` (default `5`)
- `SUBMISSION_RATE_LIMIT_SECONDS` (default `30`)
- `GRADER_TIMEOUT_SECONDS` (default `600`)
- `GRADER_RUNTIME_IMAGE` (default `penngrader2-grader-runtime:latest`)
- `ALLOWED_IMPORTS_FILE` (default `config/allowed_imports.toml`)

### Run from a source checkout

Create a virtual environment and install the repo locally:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

Start Postgres:

```bash
docker run -d --name penngrader2-pg \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=penngrader2 \
  -p 55432:5432 \
  postgres:16
```

Apply migrations:

```bash
PG_DSN='postgresql://postgres:postgres@localhost:55432/penngrader2' \
  .venv/bin/python scripts/migrate.py
```

Build the grader runtime image used by the worker:

```bash
docker build -f docker/Dockerfile.grader-runtime \
  -t penngrader2-grader-runtime:latest .
```

Run the API:

```bash
PG_DSN='postgresql://postgres:postgres@localhost:55432/penngrader2' \
API_KEY_STUDENT='student-dev-key' \
API_KEY_TA='ta-dev-key' \
API_KEY_INSTRUCTOR='instructor-dev-key' \
.venv/bin/uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Run the worker in a second shell:

```bash
PG_DSN='postgresql://postgres:postgres@localhost:55432/penngrader2' \
API_KEY_STUDENT='student-dev-key' \
API_KEY_TA='ta-dev-key' \
API_KEY_INSTRUCTOR='instructor-dev-key' \
GRADER_RUNTIME_IMAGE='penngrader2-grader-runtime:latest' \
.venv/bin/python -m app.worker.main
```

Check that the API is live:

```bash
curl -sS http://127.0.0.1:8000/healthz
```

### Run with Docker Compose

The example stack is in `docker-compose.example.yml`.

```bash
docker build -f docker/Dockerfile.grader-runtime \
  -t penngrader2-grader-runtime:latest .

docker compose -f docker-compose.example.yml up --build
```

### Single-node deployment notes

- Run PostgreSQL, the API, and the worker on the same host or Docker network.
- Keep `WORKER_CONCURRENCY=5` and `GRADER_TIMEOUT_SECONDS=600` until load testing says otherwise.
- Apply CPU and memory limits to worker-launched grader containers.
- Put a reverse proxy in front of the API for TLS, request logging, and SSE-friendly timeouts.

### Troubleshooting

- `429 Rate limited`: wait for the `Retry-After` window before resubmitting.
- Submission stuck in `queued`: verify the worker is running and can access the Docker daemon.
- Grader container failed: inspect worker logs for `error_type`, traceback, and Docker errors.
- Import validation rejected a grader: update `config/allowed_imports.toml` and rebuild the grader runtime image if new packages are required.

## Usage

### Authentication roles

Every API request requires an `X-API-Key` header.

- Student key: submit work, stream submission events, and read that student's assignment score.
- TA key: upload and fetch grader scripts.
- Instructor key: read aggregate assignment scores.

### Install the notebook client

Install directly from GitHub instead of relying on a separate package release:

```bash
pip install "git+https://github.com/RyanMarcus/penngrader2.git"
```

### Configure student notebooks

```python
import penngrader2

penngrader2.configure("http://127.0.0.1:8000", api_key="student-dev-key")
penngrader2.login(21837184)
penngrader2.submit("hw1", "problem1", 42)
```

`submit()` streams progress updates and prints a final summary instead of returning a JSON blob.

For raw API submission without the client:

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v1/student/submissions' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: student-dev-key' \
  -d '{
    "student_id": 21837184,
    "assignment_key": "hw1",
    "problem_key": "problem1",
    "submission_payload": 42
  }'
```

To stream SSE updates, use the `events_url` returned by submission creation:

```bash
curl -N -H 'X-API-Key: student-dev-key' \
  'http://127.0.0.1:8000/v1/student/submissions/<submission_id>/events?student_id=21837184'
```

To resume an interrupted stream with replay:

```bash
curl -N -H 'X-API-Key: student-dev-key' -H 'Last-Event-ID: 5' \
  'http://127.0.0.1:8000/v1/student/submissions/<submission_id>/events?student_id=21837184'
```

### Upload grader scripts

Each grader function must be named `grade_<problem_key_sanitized>` and accept `(submission, callback)`.

From Python with a TA key:

```python
import penngrader2

penngrader2.configure("http://127.0.0.1:8000", api_key="ta-dev-key")
penngrader2.upload_grader(
    "hw1",
    "problem1",
    5,
    """def grade_problem1(submission, callback):
    callback('Starting')
    answer = int(submission)
    if answer == 42:
        return (5, 'Correct')
    return (0, f'Expected 42, got {answer}')
""",
)
```

From the raw API:

```bash
curl -sS -X PUT 'http://127.0.0.1:8000/v1/ta/assignments/hw1/problems/problem1/grader' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: ta-dev-key' \
  -d @- <<'JSON'
{
  "total_points": "5",
  "source_code": "def grade_problem1(submission, callback):\n    callback('Starting')\n    answer = int(submission)\n    if answer == 42:\n        return (5, 'Correct')\n    return (0, f'Expected 42, got {answer}')\n"
}
JSON
```

Fetch the current grader for a problem:

```bash
curl -sS -H 'X-API-Key: ta-dev-key' \
  'http://127.0.0.1:8000/v1/ta/assignments/hw1/problems/problem1/grader'
```

### Fetch grades

Student aggregate assignment score:

```bash
curl -sS -H 'X-API-Key: student-dev-key' \
  'http://127.0.0.1:8000/v1/student/assignments/hw1/score?student_id=21837184'
```

Instructor aggregate assignment score for a specific student:

```bash
curl -sS -H 'X-API-Key: instructor-dev-key' \
  'http://127.0.0.1:8000/v1/instructor/assignments/hw1/students/21837184/score'
```

Scoring rules:

- Submissions are append-only.
- Assignment score is the sum of each problem's maximum earned score.
- Updating a grader does not regrade historical submissions.

## Development

### Local development workflow

Install the repo in editable mode and run tests:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

### What future agents should know

- GitHub is the distribution source of truth for the notebook client; use `pip install git+https://github.com/RyanMarcus/penngrader2.git` instead of planning around a separate PyPI release.
- `docker/Dockerfile.api` installs the service package from GitHub over HTTPS. Use `--build-arg PENNGRADER2_REF=<branch|tag|sha>` if you need to pin a specific revision.
- `config/allowed_imports.toml` controls grader import validation. If you expand the allowlist or add grader dependencies, rebuild `docker/Dockerfile.grader-runtime`.
- Database schema changes live in `app/db/migrations`, and `scripts/migrate.py` is the migration entrypoint used by both local runs and the container stack.
- Long-running grader callbacks have been validated for multi-minute SSE progress updates. A test grader emitted updates at roughly 30-second intervals through a 120-second run.

### Code map

- API entrypoint: `app/api/main.py`
- Student, TA, and instructor routes: `app/api/routes_student.py`, `app/api/routes_ta.py`, `app/api/routes_instructor.py`
- Runtime settings: `app/core/config.py`
- Queue and score queries: `app/db/queries.py`
- Worker loop and Docker execution: `app/worker/main.py`, `app/worker/runner.py`
- Grader harness: `app/grader_runtime/harness.py`
- Notebook client: `penngrader2/client.py`

### Known gaps before large-class deployment

- No high-concurrency load test yet for bursty submissions, queue latency, or DB contention.
- SSE behavior under API restarts, proxy idle timeouts, and network drops still needs deliberate testing.
- Worker crash handling under many in-flight jobs and Docker daemon interruptions needs more validation.
- Grader container hardening, observability, backups, and recovery drills are still operational work items.
