from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class StudentSubmissionCreateRequest(BaseModel):
    student_id: int
    assignment_key: str
    problem_key: str
    submission_payload: Any


class StudentSubmissionCreateResponse(BaseModel):
    submission_id: UUID
    events_url: str
    queue_position: int


class SubmissionStatusResponse(BaseModel):
    submission_id: UUID
    student_id: int
    assignment_key: str
    problem_key: str
    status: str
    score: Decimal | None = None
    feedback: str | None = None
    error_type: str | None = None
    error_traceback: str | None = None


class StudentAssignmentScoreResponse(BaseModel):
    assignment_key: str
    student_id: int
    score: Decimal


class TAGraderUpsertRequest(BaseModel):
    source_code: str = Field(min_length=1)
    total_points: Decimal = Field(ge=0)


class TAGraderResponse(BaseModel):
    assignment_key: str
    problem_key: str
    total_points: Decimal
    grader_source_code: str
    grader_updated_at: str


class InstructorScoreResponse(BaseModel):
    assignment_key: str
    student_id: int
    score: Decimal
