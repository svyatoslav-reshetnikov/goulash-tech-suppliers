"""Точка входа API. Тестовое задание Goulash.tech: прототип, не продакшн."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import auth
from app.catalog import snapshot
from app.db.session import engine
from app.routes import catalog as catalog_routes
from app.routes import categories as categories_routes
from app.routes import locations as locations_routes
from app.routes import runs as runs_routes
from app.routes import suppliers as suppliers_routes
from app.runs import service as runs
from app.routes import health

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runs.mark_interrupted()  # запуски, прерванные перезапуском, закрываются как неудачные
    await snapshot.ensure_loaded()  # справочника нет: грузит снимок из файла (без сети и модели); есть: ничего не делает
    yield
    await runs.shutdown()
    await engine.dispose()


app = FastAPI(
    title="Поиск поставщиков продуктов питания",
    description="Тестовое задание Goulash.tech. Прототип.",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(health.router, prefix="/api")  # проверка состояния и вход открыты, остальное только после входа
app.include_router(auth.router, prefix="/api")
for router in (catalog_routes.router, categories_routes.router, locations_routes.router, runs_routes.router, suppliers_routes.router):
    app.include_router(router, prefix="/api", dependencies=auth.protected)
