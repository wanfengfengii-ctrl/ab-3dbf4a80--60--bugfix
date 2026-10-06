"""Robot calibration platform: piecewise cubic Bezier trajectory audit API."""

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .audit import audit_trajectory
from .schemas import AuditRequest

HEALTH_PATH = os.getenv("HEALTH_PATH", "/health")

app = FastAPI(title="Trajectory Audit Service", version="1.0.0")


def _format_validation_error(exc: RequestValidationError | ValidationError) -> list[dict]:
    """Normalize every error into {loc, msg, type} with a locatable field path."""
    formatted: list[dict] = []
    for err in exc.errors():
        loc = list(err.get("loc", ()))
        # Ensure a stable, body-rooted path (e.g. ["body", "segments", 0, ...]).
        if not loc or loc[0] not in ("body", "query", "path", "header"):
            loc = ["body", *loc]
        formatted.append(
            {
                "loc": loc,
                "msg": err.get("msg", "invalid value"),
                "type": err.get("type", "value_error"),
            }
        )
    return formatted


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": _format_validation_error(exc)})


@app.get(HEALTH_PATH)
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/trajectories/audit")
async def audit(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": ["body"], "msg": "request body must be valid JSON", "type": "parse_error"}
                ]
            },
        )

    try:
        req = AuditRequest.model_validate(payload)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": _format_validation_error(exc)},
        )

    return audit_trajectory(req)
