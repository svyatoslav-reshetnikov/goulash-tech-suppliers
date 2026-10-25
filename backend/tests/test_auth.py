import asyncio

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app import auth
from app.config import Settings


def request(cookie: str | None = None, forwarded_for: str | None = None, proto: str | None = None) -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", f"{auth.COOKIE}={cookie}".encode()))
    if forwarded_for:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    if proto:
        headers.append((b"x-forwarded-proto", proto.encode()))
    return Request({"type": "http", "headers": headers, "client": ("10.0.0.1", 1234), "scheme": "http", "server": ("t", 80), "path": "/", "query_string": b""})


@pytest.fixture
def enabled(monkeypatch):
    stored = auth.hash_password("правильный-пароль", iterations=1000)
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(_env_file=None, auth_user="goulash", auth_password_hash=stored))
    auth._failures.clear()
    return stored


def run(coro):
    return asyncio.run(coro)


def test_password_hash_roundtrip_and_wrong_password():
    stored = auth.hash_password("секрет", iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$") and "секрет" not in stored
    assert auth.verify_password("секрет", stored)
    assert not auth.verify_password("другой", stored)
    assert not auth.verify_password("секрет", "мусор")
    assert auth.hash_password("секрет", iterations=1000) != stored  # соль у каждого хеша своя


def test_token_is_signed_expires_and_dies_with_the_password():
    stored = auth.hash_password("a", iterations=1000)
    token = auth.make_token("goulash", stored, expires_at=2_000_000_000)
    assert auth.read_token(token, stored, now=1_900_000_000) == "goulash"
    assert auth.read_token(token, stored, now=2_100_000_000) is None  # срок вышел
    assert auth.read_token(token + "0", stored, now=1) is None  # подпись испорчена
    assert auth.read_token(token.replace("Z291bGFzaA", "YWRtaW4"), stored, now=1) is None  # подмена имени
    assert auth.read_token(token, auth.hash_password("a", iterations=1000), now=1) is None  # пароль сменили: сессии недействительны
    assert auth.read_token(None, stored) is None and auth.read_token("абракадабра", stored) is None


def test_protected_routes_need_a_session_when_auth_is_enabled(enabled):
    with pytest.raises(HTTPException) as error:
        run(auth.require_auth(request()))
    assert error.value.status_code == 401
    token = auth.make_token("goulash", enabled, 2_000_000_000)
    run(auth.require_auth(request(cookie=token)))  # с сессией проходит
    with pytest.raises(HTTPException):
        run(auth.require_auth(request(cookie="чужая.1.подпись")))


def test_auth_is_off_without_a_password_hash(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(_env_file=None))
    run(auth.require_auth(request()))  # локальная работа без пароля
    assert run(auth.me(request())) == {"auth_required": False, "authenticated": True, "user": None}


def test_login_sets_httponly_cookie_and_rejects_wrong_credentials(enabled):
    body = lambda user, password: auth.LoginBody(username=user, password=password)  # noqa: E731
    response = Response()
    result = run(auth.login(body("goulash", "правильный-пароль"), request(proto="https"), response))
    assert result["authenticated"] and result["user"] == "goulash"
    cookie = response.headers["set-cookie"]
    assert auth.COOKIE in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    token = cookie.split(f"{auth.COOKIE}=")[1].split(";")[0]
    assert run(auth.me(request(cookie=token))) == {"auth_required": True, "authenticated": True, "user": "goulash"}
    assert "Secure" not in _login_cookie(enabled)  # по http без Secure: иначе вход не заработает локально

    for user, password in (("goulash", "не тот"), ("чужой", "правильный-пароль")):
        with pytest.raises(HTTPException) as error:
            run(auth.login(body(user, password), request(), Response()))
        assert error.value.status_code == 401 and "Неверный логин или пароль" in error.value.detail


def _login_cookie(stored) -> str:
    response = Response()
    run(auth.login(auth.LoginBody(username="goulash", password="правильный-пароль"), request(), response))
    return response.headers["set-cookie"]


def test_too_many_wrong_passwords_are_blocked_per_address(enabled):
    bad = auth.LoginBody(username="goulash", password="не тот")
    for _ in range(auth.MAX_FAILURES):
        with pytest.raises(HTTPException) as error:
            run(auth.login(bad, request(forwarded_for="5.5.5.5"), Response()))
        assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        run(auth.login(bad, request(forwarded_for="5.5.5.5"), Response()))
    assert error.value.status_code == 429
    good = auth.LoginBody(username="goulash", password="правильный-пароль")
    with pytest.raises(HTTPException):  # даже верный пароль с этого адреса пока не принимается
        run(auth.login(good, request(forwarded_for="5.5.5.5"), Response()))
    assert run(auth.login(good, request(forwarded_for="6.6.6.6"), Response()))["authenticated"]  # другой адрес не затронут


def test_every_api_route_except_health_and_login_requires_auth():
    from app.main import app

    open_paths = {"/api/health", "/api/auth/me", "/api/auth/login", "/api/auth/logout", "/api/docs", "/api/openapi.json", "/api/docs/oauth2-redirect"}
    unprotected = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api") or path in open_paths:
            continue
        calls = {dep.call for dep in route.dependant.dependencies}
        if auth.require_auth not in calls:
            unprotected.append(path)
    assert unprotected == []
