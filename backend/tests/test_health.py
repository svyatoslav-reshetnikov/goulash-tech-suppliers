import asyncio
import json

from app.routes.health import health


class FakeSession:
    def __init__(self, fail: bool = False):
        self._fail = fail

    async def execute(self, statement):
        if self._fail:
            raise ConnectionError("база недоступна")


def test_health_ok():
    response = asyncio.run(health(FakeSession()))
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ok", "database": "ok"}


def test_health_degraded_when_database_is_down():
    response = asyncio.run(health(FakeSession(fail=True)))
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "degraded", "database": "error"}
