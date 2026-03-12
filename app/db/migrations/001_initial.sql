CREATE TYPE submission_status AS ENUM (
  'queued',
  'running',
  'succeeded',
  'failed'
);

CREATE TABLE assignments (
  id BIGSERIAL PRIMARY KEY,
  assignment_key TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE problems (
  id BIGSERIAL PRIMARY KEY,
  assignment_id BIGINT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
  problem_key TEXT NOT NULL,
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
  submission_payload JSONB NOT NULL,
  status submission_status NOT NULL,
  queue_entered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ NULL,
  finished_at TIMESTAMPTZ NULL,
  score NUMERIC(10,4) NULL,
  feedback TEXT NULL,
  error_type TEXT NULL,
  error_traceback TEXT NULL,
  grader_source_hash TEXT NULL,
  worker_id TEXT NULL
);

CREATE INDEX idx_submissions_queue ON submissions (status, queue_entered_at, id);
CREATE INDEX idx_submissions_student_problem ON submissions (student_id, problem_id, queue_entered_at DESC);
CREATE INDEX idx_submissions_student_assignment ON submissions (student_id, assignment_id);
CREATE INDEX idx_submissions_student_recent ON submissions (student_id, queue_entered_at DESC);

CREATE TABLE submission_events (
  id BIGSERIAL PRIMARY KEY,
  submission_id UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  event_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_submission_id_id ON submission_events (submission_id, id);
