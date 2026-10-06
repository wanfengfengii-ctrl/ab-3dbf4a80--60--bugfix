"""Robot calibration platform: piecewise cubic Bezier trajectory audit API."""

import json
import os
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .audit import audit_trajectory
from .decimalio import decimal_to_literal
from .schemas import AuditRequest

HEALTH_PATH = os.getenv("HEALTH_PATH", "/health")

app = FastAPI(title="Trajectory Audit Service", version="1.0.0")

# Null bytes cannot occur verbatim in a JSON string (they are escaped), so a
# token built from them can never collide with real payload content.
_RAW_TOKEN = "\x00raw_%d\x00"


def _dumps_high_precision(obj) -> str:
    """json.dumps that emits Decimals as bare full-precision number literals."""
    raws: list[str] = []

    def convert(value):
        if isinstance(value, Decimal):
            raws.append(decimal_to_literal(value))
            return _RAW_TOKEN % (len(raws) - 1)
        if isinstance(value, dict):
            return {key: convert(val) for key, val in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(val) for val in value]
        return value

    text = json.dumps(convert(obj))
    for i, literal in enumerate(raws):
        token = json.dumps(_RAW_TOKEN % i)  # quoted, with nulls escaped
        if token not in text:
            raise RuntimeError("internal serialization error: decimal token missing")
        text = text.replace(token, literal)
    return text


class HighPrecisionJSONResponse(JSONResponse):
    """JSON response preserving every significant digit of Decimal results."""

    def render(self, content) -> bytes:
        return _dumps_high_precision(content).encode("utf-8")


def _format_validation_error(exc) -> list[dict]:
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
    return HighPrecisionJSONResponse(
        status_code=422, content={"detail": _format_validation_error(exc)}
    )


@app.get(HEALTH_PATH)
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/trajectories/audit")
async def audit(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return HighPrecisionJSONResponse(
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
        return HighPrecisionJSONResponse(
            status_code=422,
            content={"detail": _format_validation_error(exc)},
        )

    return HighPrecisionJSONResponse(audit_trajectory(req))
