from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import ROLE_TA, require_role
from app.api.schemas import TAGraderResponse, TAGraderUpsertRequest
from app.core.config import Settings, get_settings
from app.core.grader_validation import (
    GraderValidationError,
    load_allowed_imports,
    validate_grader_source,
)
from app.db.connection import get_conn
from app.db.queries import get_problem, upsert_problem_grader

router = APIRouter(prefix="/v1/ta", tags=["ta"])


@router.put("/assignments/{assignment_key}/problems/{problem_key}/grader", response_model=TAGraderResponse)
def upsert_grader(
    assignment_key: str,
    problem_key: str,
    payload: TAGraderUpsertRequest,
    _: str = Depends(require_role(ROLE_TA)),
    settings: Settings = Depends(get_settings),
):
    try:
        allowed = load_allowed_imports(settings.allowed_imports_path)
        validate_grader_source(payload.source_code, problem_key, allowed)
    except (OSError, GraderValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    with get_conn() as conn:
        metadata = upsert_problem_grader(
            conn,
            assignment_key=assignment_key,
            problem_key=problem_key,
            total_points=payload.total_points,
            source_code=payload.source_code,
        )
        conn.commit()

    return TAGraderResponse(
        assignment_key=assignment_key,
        problem_key=problem_key,
        total_points=payload.total_points,
        grader_source_code=payload.source_code,
        grader_updated_at=metadata["grader_updated_at"],
    )


@router.get("/assignments/{assignment_key}/problems/{problem_key}/grader", response_model=TAGraderResponse)
def get_grader(
    assignment_key: str,
    problem_key: str,
    _: str = Depends(require_role(ROLE_TA)),
):
    with get_conn() as conn:
        row = get_problem(conn, assignment_key, problem_key)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")

    return TAGraderResponse(
        assignment_key=assignment_key,
        problem_key=problem_key,
        total_points=row["total_points"],
        grader_source_code=row["grader_source_code"],
        grader_updated_at=row["grader_updated_at"].isoformat(),
    )
