from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import ROLE_INSTRUCTOR, require_role
from app.api.schemas import InstructorScoreResponse
from app.db.connection import get_conn
from app.db.queries import get_assignment_score

router = APIRouter(prefix="/v1/instructor", tags=["instructor"])


@router.get("/assignments/{assignment_key}/students/{student_id}/score", response_model=InstructorScoreResponse)
def get_instructor_score(
    assignment_key: str,
    student_id: int,
    _: str = Depends(require_role(ROLE_INSTRUCTOR)),
):
    with get_conn() as conn:
        score = get_assignment_score(conn, student_id, assignment_key)
    return InstructorScoreResponse(assignment_key=assignment_key, student_id=student_id, score=score)
