"""Вход по логину и паролю: подписанная cookie-сессия.

Один пользователь, заданный в `.env` (AUTH_USER и хеш пароля AUTH_PASSWORD_HASH). Если хеш не задан, вход отключён:
так удобно работать локально. Пароль в открытом виде нигде не хранится, хеш получают скриптом
`backend/scripts/hash_password.py`. Сессия подписана ключом, производным от хеша: смена пароля сразу завершает все сессии.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import get_settings

COOKIE = "goulash_session"
HASH_PREFIX = "pbkdf2_sha256"
ITERATIONS = 600_000
MAX_FAILURES, FAILURE_WINDOW = 8, 600  # не больше 8 неверных паролей за 10 минут с одного адреса

router = APIRouter(prefix="/auth", tags=["вход"])
_failures: dict[str, deque[float]] = defaultdict(deque)


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"{HASH_PREFIX}${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        prefix, iterations, salt, expected = stored.split("$")
        if prefix != HASH_PREFIX:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def _key(stored_hash: str) -> bytes:
    return hashlib.sha256(f"goulash-session:{stored_hash}".encode()).digest()


def make_token(user: str, stored_hash: str, expires_at: int) -> str:
    payload = base64.urlsafe_b64encode(user.encode()).decode().rstrip("=")
    signature = hmac.new(_key(stored_hash), f"{payload}.{expires_at}".encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{expires_at}.{signature}"


def read_token(token: str | None, stored_hash: str, now: float | None = None) -> str | None:
    """Имя пользователя из действующей подписанной сессии, иначе None."""
    try:
        payload, expires, signature = (token or "").split(".")
        expected = hmac.new(_key(stored_hash), f"{payload}.{expires}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or int(expires) < (now if now is not None else time.time()):
            return None
        return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    except (ValueError, TypeError):
        return None


def _credentials() -> tuple[str, str] | None:
    settings = get_settings()
    if settings.auth_password_hash is None or not settings.auth_password_hash.get_secret_value():
        return None
    return settings.auth_user or "admin", settings.auth_password_hash.get_secret_value()


def current_user(request: Request) -> str | None:
    credentials = _credentials()
    return read_token(request.cookies.get(COOKIE), credentials[1]) if credentials else None


async def require_auth(request: Request) -> None:
    """Зависимость для защищённых маршрутов: без входа ответ 401 (если вход включён)."""
    if _credentials() is not None and current_user(request) is None:
        raise HTTPException(status_code=401, detail="Требуется вход")


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


class LoginBody(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=500)


@router.get("/me")
async def me(request: Request) -> dict:
    if _credentials() is None:
        return {"auth_required": False, "authenticated": True, "user": None}
    user = current_user(request)
    return {"auth_required": True, "authenticated": user is not None, "user": user}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    credentials = _credentials()
    if credentials is None:
        return {"auth_required": False, "authenticated": True, "user": None}
    user, stored = credentials
    who, now = _client(request), time.time()
    recent = _failures[who]
    while recent and now - recent[0] > FAILURE_WINDOW:
        recent.popleft()
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Слишком много неверных попыток. Подождите несколько минут")
    # пароль проверяется всегда, чтобы по времени ответа нельзя было угадать логин
    password_ok = verify_password(body.password, stored)
    user_ok = hmac.compare_digest(body.username.strip().encode(), user.encode())
    if not (password_ok and user_ok):
        recent.append(now)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    recent.clear()
    days = get_settings().auth_session_days
    expires = int(now) + days * 86400
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        COOKIE, make_token(user, stored, expires), max_age=days * 86400, httponly=True, samesite="lax", secure=secure, path="/"
    )
    return {"auth_required": True, "authenticated": True, "user": user}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}


protected = [Depends(require_auth)]
