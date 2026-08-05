from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.security import (
    SessionRateLimiter,
    TrustedForwardedProtoMiddleware,
    get_anonymous_alias,
    get_anonymous_avatar,
    get_csrf_token,
)
from app.admin import router as admin_router
from app.web import router

BASE_DIR = Path(__file__).resolve().parent


class RequestBodyLimitMiddleware:
    """Bound actual request bytes before multipart parsing or disk spooling."""

    def __init__(
        self,
        app,
        *,
        regular_limit: int,
        multipart_limit: int,
    ) -> None:
        self.app = app
        self.regular_limit = regular_limit
        self.multipart_limit = multipart_limit

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        content_type = headers.get(b"content-type", b"").lower()
        limit = (
            self.multipart_limit
            if content_type.startswith(b"multipart/form-data")
            else self.regular_limit
        )

        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await HTMLResponse(
                    "Некорректный размер запроса.", status_code=400
                )(scope, receive, send)
                return
            if declared_size < 0:
                await HTMLResponse(
                    "Некорректный размер запроса.", status_code=400
                )(scope, receive, send)
                return
            if declared_size > limit:
                await HTMLResponse("Запрос слишком большой.", status_code=413)(
                    scope, receive, send
                )
                return

        buffered_messages: list[dict] = []
        received_size = 0
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            received_size += len(message.get("body", b""))
            if received_size > limit:
                await HTMLResponse("Запрос слишком большой.", status_code=413)(
                    scope, receive, send
                )
                return
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive() -> dict:
            nonlocal message_index
            if message_index < len(buffered_messages):
                message = buffered_messages[message_index]
                message_index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


def format_datetime(value: datetime) -> str:
    return value.astimezone().strftime("%d.%m.%Y · %H:%M")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.filters["datetime"] = format_datetime

    app.state.settings = settings
    app.state.templates = templates
    app.state.limiter = SessionRateLimiter(
        settings.rate_limit_count, settings.rate_limit_window_seconds
    )
    settings.avatar_storage_dir.mkdir(parents=True, exist_ok=True)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie,
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)
    app.add_middleware(
        RequestBodyLimitMiddleware,
        regular_limit=64 * 1024,
        multipart_limit=settings.avatar_max_bytes + 256 * 1024,
    )
    app.add_middleware(
        TrustedForwardedProtoMiddleware,
        trusted_proxy_networks=settings.trusted_proxy_networks_list,
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.mount(
        "/media/avatars",
        StaticFiles(directory=settings.avatar_storage_dir),
        name="avatars",
    )
    app.include_router(router)
    app.include_router(admin_router)

    @app.middleware("http")
    async def security_and_size_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'none'; style-src 'self'; "
            "img-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            title = "Страница не найдена"
            message = (
                "Запрошенная страница не существует."
                if exc.detail == "Not Found"
                else str(exc.detail)
            )
        elif exc.status_code == 405:
            title = "Метод не поддерживается"
            message = "Этот способ запроса не поддерживается."
        else:
            title = "Не удалось выполнить действие"
            message = str(exc.detail)
        return templates.TemplateResponse(
            request,
            "error.html",
            base_context(request)
            | {"title": title, "message": message, "status_code": exc.status_code},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        return templates.TemplateResponse(
            request,
            "error.html",
            base_context(request)
            | {
                "title": "Некорректный запрос",
                "message": "Проверьте адрес и отправленные данные.",
                "status_code": 422,
            },
            status_code=422,
        )

    return app


def base_context(request: Request) -> dict:
    return {
        "app_name": request.app.state.settings.app_name,
        "csrf_token": get_csrf_token(request),
        "session_user_id": request.session.get("user_id"),
        "anonymous_alias": get_anonymous_alias(request),
        "anonymous_avatar": get_anonymous_avatar(request),
        "notice": request.session.pop("notice", None),
    }


app = create_app()
