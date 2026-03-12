# PennGrader 2

This is a tool for automatically grading CIS assignments at UPenn. There are
three primary users of the system:

* Students, who will write their homework assignments in Colab/Jupyter
  notebooks, and run special instructor-coded cells to submit values (such as a
  variable or cell source content) to the auto-grader. After submission, the
  student should see the progress of their submission (e.g., a progress bar showing grading progress, which may take several minutes or just a few seconds), as well as their overall score on the assignment so far.

* TAs, who write the assignments and set up grading scripts. Grading scripts are
  Python functions that take a student's submission as input and return a score,
  and potentially a hint to the student. Each grading function should also have
  a "callback" function parameter that can be used to update the student on
  grading progress.

* Instructors, who need to query student grades via automatic scripts (such as
  GradeScope auto graders).

PennGrader 2 is a single-node service that runs on a Docker image with access to
a PostgreSQL database. 

## Student interface

Students will copy an instructor-provided notebook that has instructions, such
as for implementing a particular algorithm or analyzing a particular piece of
data. For example, the instructions might ask the student to download a dataset,
load it into a DataFrame, and compute some summary statistic. After the empty
cell for the student submission, there will be a submission cell that looks
something like this:

```
penngrader2.submit("hw1", "problem1", student_answer)
```

When ran, this line should submit something to the grading service. The grading
service should then stream updates back to the client (e.g., with websockets or
server-sent events), eventually displaying the student's score and any feedback
provided by the grading script. This should also show the student their current
assignment score.

Progress updates should use server-sent events (SSE). The notebook client must
support reconnecting to the same submission stream if the client is interrupted
or if the server restarts. Queue status updates (such as queue position and
estimated wait state) should be sent before grading starts.

Students will identify themselves to `penngrader2` by calling a function like this at the start of their notebook:

```
penngrader2.login(21837184) # student ID number
```

For now, the submitted `student_id` is trusted as-is.

## TA Interface

TAs will write grading scripts. Each grading script is a Python function with
imports from a fixed set of available libraries. The scripts can look something
like this:

```
import duckdb

@grader(total_points=5)
def grade_problem1(submission, callback):
  callback("Starting grading...")
  conn = duckdb.connect("/path/to/some/stored/serverside/data.db")
  try:
    res = conn.sql(submission)
    callback("Query completed")
    if res[0] != 100:
      return (1, f"expected answer: 100, got: {res[0]}")
    else:
      return (5, "Correct answer")
  except Exception as e:
    return (0, f"An error occurred while running your query: {e}")
  
  
@grader(total_points=10)
def grade_problem2(submission, callback):
  callback("Starting grading...")
  # ...
```

These grading scripts could do almost anything, including calling an LLM or
making some other type of API request. They can run for arbitrary amounts of
time (usually between 1 second and 5 minutes).

Grading scripts should execute in isolated containers, not in the main API
process. This is for robustness (prevent grader failures from bringing down the
service), not for adversarial sandboxing.

Anytime `callback` is used, the student should see an update in their notebook.
The final return value is the number of points the student earns for the
problem, and a possible status message.

## Instructor interface

Instructors need an API interface to fetch a score for a particular student's
submission. This API will be called by things like the GradeScope autograder.

# Implementation notes

* Use Python, with dependencies managed via `uv`. Try to use standard libraries.
  Use FastAPI for the HTTP API.

* All data should be stored in the PostgreSQL database. If the server is
  restarted, all data and active grading submissions should be preserved. This
  implies using PostgreSQL as a job queue.

* Submission attempts are append-only. Assignment scoring should use each
  student's best attempt per problem (maximum score), and sum those best scores.

* Student submissions should be rate-limited (default: 1 submission per student
  every 30 seconds; make this easy to configure).

* The worker system should process a fixed maximum number of submissions
  concurrently (default: 5). Additional submissions remain queued in PostgreSQL
  until a worker slot is available. Students should receive queue progress
  updates while waiting.

* Maximum runtime per problem grading execution is 10 minutes. If a worker or
  grading container crashes, do not automatically retry; mark the attempt as
  failed and report a useful error status to the student, including traceback
  information when available.

* Grader execution environments should install packages from a fixed allowlist.
  The allowlist should be easy to update (e.g., config-backed rather than
  hard-coded deep in worker logic). The grader environment should start up
  quickly -- we should not build a container each time. 

* The server should be packaged inside a Docker container (i.e., a Dockerfile to
  build the project.)

* There should be separate API keys for students (all students share an API
  key), TAs, and instructors. Student keys should only allow submission and
  getting the results of that submission. The TA key should only allow updating
  grade scripts. The instructor key should only allow querying student's grades
  (aggregate assignment scores only).

* API keys can be static secrets configured at deploy time.
