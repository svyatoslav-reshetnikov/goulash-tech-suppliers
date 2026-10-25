import asyncio
import json

from app.routes.health import health


class FakeSession:
    def __init__(self, fail: bool = False):
        self._fail = fail

    async def execute(self, statement):
        if self._fail:
            raise ConnectionError("база недоступна")


def test_health_ok(monkeypatch):
    monkeypatch.delenv("IMAGE_TAG", raising=False)
    response = asyncio.run(health(FakeSession()))
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ok", "database": "ok", "version": "dev"}


def test_health_degraded_when_database_is_down(monkeypatch):
    monkeypatch.delenv("IMAGE_TAG", raising=False)
    response = asyncio.run(health(FakeSession(fail=True)))
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "degraded", "database": "error", "version": "dev"}


def test_health_reports_the_deployed_version(monkeypatch):
    """The deployment compares it with the commit it just rolled out, so a silently skipped restart is noticed."""
    monkeypatch.setenv("IMAGE_TAG", "6aec2cc")
    assert json.loads(asyncio.run(health(FakeSession())).body)["version"] == "6aec2cc"
