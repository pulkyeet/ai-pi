"""Typed API errors (phase doc's "Error handling"): every response carries a
stable `code`, never a stack trace, internal identifier, or vendor error
text — a vendor message can leak an API key fragment or an internal URL.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class APIError(Exception):
    """Base for every error this API raises on purpose. `status_code`/`code`
    are class-level defaults, overridable per instance."""

    status_code: int = 400
    code: str = "error"

    def __init__(
        self, message: str, *, code: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class AuthRequiredError(APIError):
    status_code = 401
    code = "auth_required"


class InvalidTokenError(APIError):
    status_code = 401
    code = "invalid_token"


class ForbiddenError(APIError):
    status_code = 403
    code = "forbidden"


class NotFoundError(APIError):
    status_code = 404
    code = "not_found"


class QueryRejectedAPIError(APIError):
    status_code = 422
    code = "query_rejected"


class TurnstileFailedError(APIError):
    status_code = 403
    code = "turnstile_failed"


class QuotaExceededError(APIError):
    status_code = 429
    code = "quota_exceeded"


class KillSwitchActiveError(APIError):
    status_code = 503
    code = "live_runs_paused"


class ConflictError(APIError):
    status_code = 409
    code = "conflict"


def _envelope(code: str, message: str, *, correlation_id: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "correlation_id": correlation_id}}


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, APIError)
    correlation_id = uuid.uuid4().hex
    logger.info(
        "api_error", code=exc.code, message=exc.message, path=request.url.path, cid=correlation_id
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, correlation_id=correlation_id),
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    correlation_id = uuid.uuid4().hex
    return JSONResponse(
        status_code=422,
        content=_envelope(
            "invalid_request", "request body failed validation", correlation_id=correlation_id
        ),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    correlation_id = uuid.uuid4().hex
    logger.error(
        "unhandled_error",
        error_type=type(exc).__name__,
        path=request.url.path,
        cid=correlation_id,
    )
    return JSONResponse(
        status_code=500,
        content=_envelope(
            "internal_error", "an unexpected error occurred", correlation_id=correlation_id
        ),
    )
