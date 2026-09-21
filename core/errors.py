import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def api_error(status_code: int, code: str, message: str, **context) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **context},
    )


def _correlation_id(request: Request) -> str:
    supplied = request.headers.get("X-Correlation-ID")
    if supplied and _CORRELATION_ID_PATTERN.fullmatch(supplied):
        return supplied
    return str(uuid4())


def _response(
    status_code: int,
    code: str,
    message: str,
    request: Request,
    headers=None,
    context=None,
):
    correlation_id = _correlation_id(request)
    response_headers = dict(headers or {})
    response_headers["X-Correlation-ID"] = correlation_id
    error = {
        "code": code,
        "message": message,
        "correlation_id": correlation_id,
    }
    if isinstance(context, dict):
        error.update(context)
    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content={
            "error": error
        },
    )


async def api_http_exception_handler(
    request: Request, exc: StarletteHTTPException
):
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code")
        message = exc.detail.get("message")
        if isinstance(code, str) and isinstance(message, str):
            context = {
                key: value
                for key, value in exc.detail.items()
                if key not in {"code", "message"}
                and isinstance(value, (str, int, float, bool, type(None)))
            }
            return _response(
                exc.status_code,
                code,
                message,
                request,
                exc.headers,
                context,
            )
    return _response(
        exc.status_code,
        "HTTP_ERROR",
        "The request could not be completed.",
        request,
        exc.headers,
    )


async def unexpected_exception_handler(request: Request, _exc: Exception):
    return _response(
        500,
        "INTERNAL_SERVER_ERROR",
        "An unexpected error occurred.",
        request,
    )


async def request_validation_exception_handler(
    request: Request, _exc: RequestValidationError
):
    return _response(
        422,
        "REQUEST_VALIDATION_ERROR",
        "The request contains invalid or missing fields.",
        request,
    )


async def storage_unavailable_handler(request: Request, _exc: Exception):
    return _response(503, "STORAGE_UNAVAILABLE", "File storage is temporarily unavailable. Please retry.", request)


def register_error_handlers(app: FastAPI) -> None:
    from services.object_storage import StorageUnavailableError
    app.add_exception_handler(StorageUnavailableError, storage_unavailable_handler)
    app.add_exception_handler(HTTPException, api_http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, api_http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, request_validation_exception_handler
    )
    app.add_exception_handler(Exception, unexpected_exception_handler)
