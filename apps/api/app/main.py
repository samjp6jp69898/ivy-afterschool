"""BACKEND-020：``create_app()`` 工廠、lifespan、router 彙整與 middleware 掛載。

``just api`` 與 Dockerfile 以 ``uvicorn app.main:create_app --factory`` 啟動。部署前提：api 單一
實例、uvicorn ``--workers 1``（INFRA-031 / 032），in-process broadcaster 與 APScheduler 依此成立；
nginx 以同源反代 ``/api`` 與 ``/api/ws/``（INFRA-033）。

- lifespan：``configure_logging`` → ``install_tx_hooks`` → 記錄 main event loop（BACKEND-223）→
  broadcaster ``start()`` → ``start_background`` 時 ``import app.jobs`` 後 ``start_scheduler``；
  shutdown 反向（scheduler → broadcaster → engine.dispose）。``start_background`` 預設
  ``app_env != "test"``。
- middleware（外到內）：``RequestContextMiddleware`` → ``SecurityMiddleware`` → ``CORSMiddleware``
  （``cors_origins`` 為空時不掛）。Starlette 的 ``ServerErrorMiddleware`` 在最外層，未處理例外的 500
  不經過上述 middleware，由 BACKEND-003 的 500 handler 自行補 ``X-Request-ID`` 與安全標頭。
- Sentry：``sentry_dsn`` 有值時 init，``before_send`` / ``before_breadcrumb`` 掛 BACKEND-004 的
  ``scrub_sentry_event`` / ``scrub_sentry_breadcrumb``（LoggingIntegration 會繞過 handler 級的
  RedactingFilter）。
- 文件：production 關閉 ``/docs`` / ``/redoc`` / ``/openapi.json``；其他環境開在 ``/api/docs``。
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.admin import admin_router
from app.api.health import router as health_router
from app.api.parent import parent_router
from app.api.ws import ws_router
from app.core.clock import get_clock
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, get_engine
from app.core.errors import register_exception_handlers
from app.core.logging import (
    RequestContextMiddleware,
    configure_logging,
    scrub_sentry_breadcrumb,
    scrub_sentry_event,
)
from app.core.scheduler import shutdown_scheduler, start_scheduler
from app.core.security_middleware import SecurityMiddleware
from app.core.tx_hooks import install_tx_hooks
from app.realtime.broadcaster import get_broadcaster, set_main_loop

APP_TITLE = "afterschool-api"
_DOCS_URL = "/api/docs"
_OPENAPI_URL = "/api/openapi.json"
_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
_CORS_HEADERS = ["Content-Type", "X-Request-ID"]


def _session_factory() -> Session:
    return SessionLocal(bind=get_engine())


def _init_sentry(settings: Settings) -> None:
    if settings.sentry_dsn is None:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        send_default_pii=False,
        traces_sample_rate=0.0,
        before_send=scrub_sentry_event,
        before_breadcrumb=scrub_sentry_breadcrumb,
    )


def _make_lifespan(
    settings: Settings, start_background: bool
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        _init_sentry(settings)
        install_tx_hooks()
        set_main_loop(asyncio.get_running_loop())
        broadcaster = get_broadcaster()
        await broadcaster.start()
        scheduler = None
        if start_background:
            importlib.import_module("app.jobs")  # 各工作模組在此 import 時以 @scheduled_job 註冊
            scheduler = start_scheduler(session_factory=_session_factory, clock=get_clock())
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            if scheduler is not None:
                shutdown_scheduler(scheduler)
            await broadcaster.stop()
            set_main_loop(None)
            if get_engine.cache_info().currsize:
                get_engine().dispose()

    return lifespan


def create_app(
    *, settings: Settings | None = None, start_background: bool | None = None
) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    if start_background is None:
        start_background = settings.app_env != "test"
    production = settings.is_production

    app = FastAPI(
        title=APP_TITLE,
        docs_url=None if production else _DOCS_URL,
        redoc_url=None,
        openapi_url=None if production else _OPENAPI_URL,
        lifespan=_make_lifespan(settings, start_background),
    )
    app.state.settings = settings

    # add_middleware 後加的在外層：這裡由內往外加，實際順序 RequestContext → Security → CORS
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=_CORS_METHODS,
            allow_headers=_CORS_HEADERS,
        )
    app.add_middleware(SecurityMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(parent_router)
    app.include_router(ws_router)
    return app
