"""Application error types and the handlers that render them."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import get_logger

logger = get_logger(__name__)

HTTP_422 = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


class AppError(Exception):
    """Base class for expected, client-visible failures."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "app_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationError(AppError):
    status_code = HTTP_422
    code = "validation_error"


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class PermissionError_(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class UpstreamError(AppError):
    """A blockchain data provider failed or rate-limited us."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"


class UnsupportedChainError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "unsupported_chain"


def _payload(code: str, message: str, details: dict | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_422,
            content=_payload(
                "validation_error", "Request payload is invalid", {"errors": exc.errors()}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=str(request.url.path), error=str(exc))
        # #region agent log
        try:
            import json as _json, time as _time
            from pathlib import Path as _Path
            _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log").open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"B","location":"errors.py:_unhandled","message":"unhandled_500","data":{"path":str(request.url.path),"error":str(exc)[:300],"err_type":type(exc).__name__},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("internal_error", "An unexpected error occurred"),
        )
