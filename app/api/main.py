from __future__ import annotations

from fastapi import FastAPI

from app.api.routes_instructor import router as instructor_router
from app.api.routes_student import router as student_router
from app.api.routes_ta import router as ta_router
from app.core.config import get_settings
from app.core.logging import configure_logging


configure_logging()
settings = get_settings()
app = FastAPI(title=settings.app_name)
app.include_router(student_router)
app.include_router(ta_router)
app.include_router(instructor_router)


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
