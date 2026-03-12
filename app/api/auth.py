from __future__ import annotations

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


ROLE_STUDENT = "student"
ROLE_TA = "ta"
ROLE_INSTRUCTOR = "instructor"


def _resolve_role(api_key: str | None, settings: Settings) -> str | None:
    if api_key == settings.api_key_student:
        return ROLE_STUDENT
    if api_key == settings.api_key_ta:
        return ROLE_TA
    if api_key == settings.api_key_instructor:
        return ROLE_INSTRUCTOR
    return None


def require_role(expected_role: str):
    def dependency(
        api_key: str | None = Security(api_key_header),
        settings: Settings = Depends(get_settings),
    ) -> str:
        actual = _resolve_role(api_key, settings)
        if actual != expected_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden for this API key",
            )
        return actual

    return dependency
