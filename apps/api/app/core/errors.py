"""BACKEND-003：AppError 與全域 exception handlers。

所有 API 錯誤回應統一為 ``{"error": {"code", "message", "details"}}``（domain_spec §2）。
service 層只拋 ``AppError``（或其子類），不得拋 ``HTTPException``。

| 例外 | status | code |
|---|---|---|
| AppError | exc.status | exc.code |
| RequestValidationError | 422 | validation_error（details 去掉 input / ctx，不回顯輸入） |
| HTTPException 404 / 405 / 其他 | 原 status | not_found / method_not_allowed / http_<status> |
| sqlalchemy IntegrityError | 409 | conflict（不回 SQL 原文） |
| 其他 Exception | 500 | internal_error（固定文案，server 端 logger.exception 帶 request id） |

未處理例外的 500 由 Starlette 最外層的 ``ServerErrorMiddleware`` 送出，不經過
``RequestContextMiddleware`` 與 ``SecurityMiddleware``，所以 500 handler 自己補 ``X-Request-ID``
（取 ``request.state.request_id``）與 BACKEND-019 的安全標頭（BACKEND-020）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import REQUEST_ID_HEADER
from app.core.security_middleware import security_headers_for

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "系統發生錯誤，請稍後再試"
VALIDATION_ERROR_MESSAGE = "輸入資料格式有誤"
_HTTP_STATUS_CODES: dict[int, tuple[str, str]] = {
    404: ("not_found", "找不到資源"),
    405: ("method_not_allowed", "不支援的操作方式"),
}


class AppError(Exception):
    """業務錯誤基底。code 為 snake_case 英文，message 為給使用者看的繁體中文。"""

    def __init__(self, code: str, message: str, *, status: int = 400, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details

    def headers(self) -> dict[str, str]:
        """附加到回應的 header；子類覆寫（例如 429 的 Retry-After）。"""
        return {}


class NotFoundError(AppError):
    """404；code 由呼叫端給（例如 student_not_found），不洩漏資源是否存在以外的資訊。"""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(code, message, status=404, details=details)


class ConflictError(AppError):
    """409。"""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(code, message, status=409, details=details)


class ForbiddenError(AppError):
    """403，預設 code=permission_denied。"""

    def __init__(
        self,
        message: str = "沒有執行此操作的權限",
        *,
        code: str = "permission_denied",
        details: Any = None,
    ) -> None:
        super().__init__(code, message, status=403, details=details)


class UnauthenticatedError(AppError):
    """401，預設 code=unauthenticated。"""

    def __init__(
        self, message: str = "請重新登入", *, code: str = "unauthenticated", details: Any = None
    ) -> None:
        super().__init__(code, message, status=401, details=details)


class RateLimitedError(AppError):
    """429；retry_after_seconds 同時寫進 details 與 Retry-After header。"""

    def __init__(
        self, message: str, *, retry_after_seconds: int, code: str = "too_many_attempts"
    ) -> None:
        super().__init__(
            code, message, status=429, details={"retry_after_seconds": retry_after_seconds}
        )
        self.retry_after_seconds = retry_after_seconds

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after_seconds)}


def _request_id(request: Request) -> str:
    """request logging middleware 注入的 request id；缺值時自行產生，確保 log 可關聯。"""
    existing = getattr(request.state, "request_id", None)
    if existing:
        return str(existing)
    return request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]


def _envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    # details 可能帶 datetime / UUID 等非 JSON 物件；過 jsonable_encoder 避免 render 二次拋錯
    content = {"error": {"code": code, "message": message, "details": jsonable_encoder(details)}}
    return JSONResponse(status_code=status, content=content)


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101  FastAPI 以註冊型別分派
    response = _envelope(exc.status, exc.code, exc.message, exc.details)
    response.headers.update(exc.headers())
    return response


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # 只保留 loc / msg / type：input 會回顯密碼等輸入值，ctx 可能含非 JSON 物件
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return _envelope(422, "validation_error", VALIDATION_ERROR_MESSAGE, details)


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code, message = _HTTP_STATUS_CODES.get(
        exc.status_code, (f"http_{exc.status_code}", str(exc.detail))
    )
    response = _envelope(exc.status_code, code, message)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    logger.warning(
        "未被 service 轉譯的 IntegrityError %s %s request_id=%s",
        request.method,
        request.url.path,
        request_id,
        exc_info=exc,
    )
    return _envelope(409, "conflict", "資料與既有紀錄衝突")


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    logger.exception(
        "未處理的例外 %s %s request_id=%s: %s",
        request.method,
        request.url.path,
        request_id,
        exc,
        exc_info=exc,
    )
    response = _envelope(500, "internal_error", INTERNAL_ERROR_MESSAGE)
    response.headers[REQUEST_ID_HEADER] = request_id
    for name, value in security_headers_for(request.url.path):
        response.headers[name.decode("latin-1")] = value.decode("latin-1")
    return response


def register_exception_handlers(app: FastAPI) -> None:
    """在 create_app() 呼叫一次，把 handler 註冊到 FastAPI app。"""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
