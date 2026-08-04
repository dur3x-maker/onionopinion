from __future__ import annotations

import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status

_password_hasher = PasswordHasher()
_login_pattern = re.compile(r"^[a-zA-Z0-9_.-]+$")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_login(login: str) -> str | None:
    if not 3 <= len(login) <= 32:
        return "Логин должен содержать от 3 до 32 символов."
    if not _login_pattern.fullmatch(login):
        return "В логине допустимы латинские буквы, цифры, точка, дефис и подчёркивание."
    return None


def validate_password(password: str) -> str | None:
    if not 10 <= len(password) <= 128:
        return "Пароль должен содержать от 10 до 128 символов."
    return None


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def require_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверный CSRF-токен")


def get_anonymous_alias(request: Request) -> str:
    alias = request.session.get("anonymous_alias")
    if not alias:
        alias = f"anon-{secrets.token_hex(2)}"
        request.session["anonymous_alias"] = alias
    return alias


def get_anonymous_avatar(request: Request) -> str:
    avatar = request.session.get("anonymous_avatar")
    if not avatar:
        avatar = f"anon-avatar-{secrets.randbelow(8) + 1}"
        request.session["anonymous_avatar"] = avatar
    return avatar


class SessionRateLimiter:
    """Small-process limiter keyed by an opaque session value, never by IP."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, request: Request, action: str) -> None:
        key = request.session.get("rate_key")
        if not key:
            key = secrets.token_urlsafe(18)
            request.session["rate_key"] = key
        bucket_key = f"{key}:{action}"
        now = time.monotonic()
        with self._lock:
            events = self._events[bucket_key]
            while events and events[0] <= now - self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Слишком много действий. Подождите минуту.",
                )
            events.append(now)
