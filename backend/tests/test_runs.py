"""Запуски агента на настоящем Postgres: поставщики с цитатами, кэш страниц, события и итог запуска (модель и сеть — заглушки)."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.models import AcceptedField, SupplierDraft
from app.agent.pages import Page
from app.config import MissingSettingError, Settings
from app.db.models import Base, Category, FetchedPage, Run, RunEvent, RunSupplier, Supplier, SupplierField
from app.runs import service
from app.runs.store import DbPageCache, DbSink, EventWriter
from tests.test_agent import (
    CONTACTS_HTML,
    CONTACTS_URL,
    GOOD_FIELDS,
    HOME_HTML,
    HOME_URL,
    FakeFetcher,
    FakeSearch,
    ScriptedLLM,
    hit,
    tool,
)

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="нужна TEST_DATABASE_URL")


def with_db(fn):
    def wrapper():
        async def run():
            engine = create_async_engine(TEST_DB)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            try:
                await fn(async_sessionmaker(engine, expire_on_commit=False))
            finally:
                await engine.dispose()

        asyncio.run(run())

    wrapper.__name__ = fn.__name__
    return wrapper


async def new_run(factory, **overrides) -> int:
    async with factory() as session:
        category = await session.scalar(select(Category).where(Category.normalized_name == "моцарелла"))
        if category is None:
            category = Category(name="Моцарелла", normalized_name="моцарелла", synonyms=["Сыр моцарелла"], origin="derived")
            session.add(category)
            await session.flush()
        run = Run(category_id=category.id, category_name="Моцарелла", location_label="Казань, Республика Татарстан",
                  regions=[{"id": "RU-TA", "name": "Республика Татарстан", "role": "primary"}], status="queued", **overrides)
        session.add(run)
        await session.commit()
        return run.id


def draft(domain="syrmaster-kazan.test", **kwargs) -> SupplierDraft:
    fields = kwargs.get("fields") or [
        AcceptedField("name", "ООО «СырМастер Казань»", "ООО «СырМастер Казань»", f"https://{domain}/"),
        AcceptedField("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23", f"https://{domain}/contacts"),
        AcceptedField("inn", "7707083893", "ИНН 7707083893", f"https://{domain}/contacts"),
    ]
    return SupplierDraft(website=f"https://{domain}/", domain=domain, fields=fields)


@needs_db
@with_db
async def test_db_sink_creates_merges_updates_quote_and_links_run(factory):
    run_id = await new_run(factory)
    sink = DbSink(factory, run_id)
    first = await sink.save(draft())
    assert first.created and first.name == "ООО «СырМастер Казань»"

    more = draft(fields=[
        AcceptedField("phone", "+7 (843) 555-01-23", "Новая цитата: +7 (843) 555-01-23", "https://syrmaster-kazan.test/about"),
        AcceptedField("email", "sales@syrmaster-kazan.test", "sales@syrmaster-kazan.test", "https://syrmaster-kazan.test/contacts"),
    ])
    second = await sink.save(more)
    assert not second.created and second.supplier_id == first.supplier_id

    async with factory() as session:
        fields = (await session.execute(select(SupplierField).where(SupplierField.supplier_id == first.supplier_id))).scalars().all()
        phone = [f for f in fields if f.field == "phone"]
        assert len(phone) == 1 and phone[0].quote.startswith("Новая цитата") and phone[0].source_url.endswith("/about")
        assert {f.field for f in fields} == {"name", "phone", "inn", "email"}
        assert (await session.scalar(select(Supplier).where(Supplier.id == first.supplier_id))).inn == "7707083893"
        assert len((await session.execute(select(RunSupplier))).scalars().all()) == 1  # связь с запуском одна


@needs_db
@with_db
async def test_db_sink_rejects_same_inn_on_another_domain(factory):
    run_id = await new_run(factory)
    sink = DbSink(factory, run_id)
    await sink.save(draft())
    twin = await sink.save(draft(domain="twin.test"))
    assert twin.duplicate_of == "syrmaster-kazan.test"
    async with factory() as session:
        assert await session.scalar(select(Supplier).where(Supplier.domain == "twin.test")) is None


@needs_db
@with_db
async def test_page_cache_roundtrip_and_ttl(factory):
    cache = DbPageCache(factory)
    page = Page(url="https://a.test/", title="A", text="Контакты", links=[("https://a.test/c", "Контакты")], site="a.test")
    assert await cache.get(page.url) is None
    await cache.put(page)
    cached = await cache.get(page.url)
    assert cached.text == "Контакты" and cached.links == [("https://a.test/c", "Контакты")] and cached.site == "a.test"
    await cache.put(Page(url=page.url, title="B", text="Новый текст", site="a.test"))  # обновление, а не дубль
    assert (await cache.get(page.url)).text == "Новый текст"
    async with factory() as session:
        await session.execute(update(FetchedPage).values(fetched_at=datetime.now(UTC) - timedelta(days=8)))
        await session.commit()
    assert await cache.get(page.url) is None  # устарела


@needs_db
@with_db
async def test_event_writer_numbers_events(factory):
    run_id = await new_run(factory)
    emit = EventWriter(factory, run_id)
    await emit("search", "Ищу", query="x")
    await emit("fetch", "Читаю")
    async with factory() as session:
        events = (await session.execute(select(RunEvent).order_by(RunEvent.seq))).scalars().all()
    assert [(e.seq, e.type) for e in events] == [(1, "search"), (2, "fetch")] and events[0].payload["query"] == "x"


def install(monkeypatch, llm, pages=None, hits=None):
    fetcher = FakeFetcher(pages if pages is not None else {HOME_URL: HOME_HTML, CONTACTS_URL: CONTACTS_HTML})
    deps = service.Deps(llm=llm, searcher=FakeSearch(hits or [hit("syrmaster-kazan.test")]), fetcher=fetcher)
    monkeypatch.setattr(service, "deps_factory", lambda settings: deps)
    return fetcher


def script():
    return [tool("search_web", query="моцарелла оптом Казань"), tool("fetch_page", url=HOME_URL),
            tool("fetch_page", url=CONTACTS_URL), tool("save_supplier", website=HOME_URL, fields=GOOD_FIELDS)]


SETTINGS = Settings(_env_file=None, agent_target_suppliers=1)


@needs_db
@with_db
async def test_execute_run_end_to_end_and_second_run_uses_page_cache(factory, monkeypatch=None):
    monkeypatch = pytest.MonkeyPatch()
    try:
        fetcher = install(monkeypatch, ScriptedLLM(script()))
        run_id = await new_run(factory)
        await service.execute_run(factory, run_id, SETTINGS)

        async with factory() as session:
            run = await session.get(Run, run_id)
            events = (await session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))).scalars().all()
            supplier = await session.scalar(select(Supplier))
            fields = (await session.execute(select(SupplierField).where(SupplierField.supplier_id == supplier.id))).scalars().all()
        assert run.status == "done" and run.stop_reason == "target_reached" and run.started_at and run.finished_at
        assert run.stats["suppliers_saved"] == 1 and run.stats["fetches"] == 2 and len(fetcher.calls) == 2
        assert run.stats["cost_rub"] >= 0 and "cost_search_rub" in run.stats
        types = [e.type for e in events]
        assert types[0] == "run_started" and types[-1] == "run_finished" and "supplier_saved" in types
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert {f.field for f in fields} >= {"name", "phone", "email", "inn", "moq"} and all(f.quote and f.source_url for f in fields)

        # второй запуск: страницы берутся из кэша базы, сеть не трогаем, поставщик дополняется, а не дублируется
        fetcher2 = install(monkeypatch, ScriptedLLM(script()))
        run2 = await new_run(factory)
        await service.execute_run(factory, run2, SETTINGS)
        async with factory() as session:
            assert (await session.get(Run, run2)).status == "done"
            assert len((await session.execute(select(Supplier))).scalars().all()) == 1
            assert len((await session.execute(select(RunSupplier))).scalars().all()) == 2
        assert fetcher2.calls == []
    finally:
        monkeypatch.undo()


@needs_db
@with_db
async def test_execute_run_failure_paths_are_recorded(factory, monkeypatch=None):
    monkeypatch = pytest.MonkeyPatch()
    try:
        def no_keys(settings):
            raise MissingSettingError("Не задано: LLM_API_KEY")

        monkeypatch.setattr(service, "deps_factory", no_keys)
        run_id = await new_run(factory)
        await service.execute_run(factory, run_id, SETTINGS)
        async with factory() as session:
            run = await session.get(Run, run_id)
            events = (await session.execute(select(RunEvent).where(RunEvent.run_id == run_id))).scalars().all()
        assert run.status == "failed" and "LLM_API_KEY" in run.error and [e.type for e in events] == ["run_failed"]

        install(monkeypatch, ScriptedLLM([RuntimeError("модель недоступна")]))
        run2 = await new_run(factory)
        await service.execute_run(factory, run2, SETTINGS)
        async with factory() as session:
            run = await session.get(Run, run2)
        assert run.status == "failed" and run.stop_reason == "llm_error" and "недоступна" in run.error
    finally:
        monkeypatch.undo()


@needs_db
@with_db
async def test_interrupted_runs_are_closed_on_startup(factory):
    run_id = await new_run(factory)
    async with factory() as session:
        await session.execute(update(Run).values(status="running"))
        await session.commit()
    await service.mark_interrupted(factory)
    async with factory() as session:
        run = await session.get(Run, run_id)
        assert run.status == "failed" and "перезапуском" in run.error and await service.active_runs(session) == 0


@needs_db
@with_db
async def test_stream_replays_history_follows_live_events_and_ends_with_status(factory):
    run_id = await new_run(factory)
    emit = EventWriter(factory, run_id)
    await emit("run_started", "Запуск агента")
    await emit("search", "Ищу", query="x")

    async def finish_later():
        await asyncio.sleep(0.15)
        await emit("supplier_saved", "Сохранён поставщик", domain="a.test")
        await emit("run_finished", "Готово")
        async with factory() as session:
            await session.execute(update(Run).where(Run.id == run_id).values(status="done", stop_reason="finished"))
            await session.commit()

    task = asyncio.create_task(finish_later())
    got = [e async for e in service.stream_events(factory, run_id, poll=0.03, ping_every=0.05)]
    await task
    types = [e["type"] for e in got if e]
    assert types == ["run_started", "search", "supplier_saved", "run_finished", "stream_end"]  # история, затем живые, затем конец
    assert [e["seq"] for e in got if e and "seq" in e] == [1, 2, 3, 4]
    assert got[-1]["status"] == "done" and got[-1]["stop_reason"] == "finished"
    assert None in got  # пока ничего не происходило, приходил heartbeat


@needs_db
@with_db
async def test_stream_resumes_after_given_seq_and_ends_at_once_for_finished_run(factory):
    run_id = await new_run(factory)
    emit = EventWriter(factory, run_id)
    for i in range(3):
        await emit("search", f"шаг {i}")
    async with factory() as session:
        await session.execute(update(Run).where(Run.id == run_id).values(status="failed", stop_reason="llm_error"))
        await session.commit()
    got = [e async for e in service.stream_events(factory, run_id, after=2, poll=0.01)]
    assert [e["type"] for e in got] == ["search", "stream_end"] and got[0]["seq"] == 3 and got[-1]["status"] == "failed"
    assert [e async for e in service.stream_events(factory, 999999, poll=0.01)][0]["status"] == "missing"


@needs_db
@with_db
async def test_concurrent_saves_of_the_same_new_supplier_do_not_collide(factory):
    run_a, run_b = await new_run(factory), await new_run(factory)
    outcomes = await asyncio.gather(*[DbSink(factory, run).save(draft()) for run in (run_a, run_b) for _ in range(3)])
    assert sum(o.created for o in outcomes) == 1 and len({o.supplier_id for o in outcomes}) == 1
    async with factory() as session:
        assert len((await session.execute(select(Supplier))).scalars().all()) == 1
        fields = (await session.execute(select(SupplierField))).scalars().all()
        assert len(fields) == 3  # name, phone, inn: одинаковые значения не размножились
        assert len((await session.execute(select(RunSupplier))).scalars().all()) == 2  # поставщик привязан к обоим запускам


@needs_db
@with_db
async def test_run_suppliers_endpoint_returns_ranking_in_order_with_recommendation(factory):
    from app.routes.runs import run_suppliers

    run_id = await new_run(factory)
    async with factory() as session:  # регион запуска: Татарстан (основной) и его соседи
        from app.locations import get_locations

        ta = next(r for r in get_locations().regions.values() if r.name == "Республика Татарстан")
        run = await session.get(Run, run_id)
        run.regions = [{"id": ta.id, "name": ta.name, "role": "primary"}]
        await session.commit()
    sink = DbSink(factory, run_id)
    await sink.save(draft(domain="far.test", fields=[
        AcceptedField("name", "Далёкий", "Далёкий", "https://far.test/"),
        AcceptedField("email", "a@far.test", "a@far.test", "https://far.test/")]))
    await sink.save(draft(domain="near.test", fields=[
        AcceptedField("name", "Местный", "Местный", "https://near.test/"),
        AcceptedField("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23", "https://near.test/c"),
        AcceptedField("email", "a@near.test", "a@near.test", "https://near.test/c"),
        AcceptedField("address", "г. Казань, ул. Бухарская 4В", "г. Казань, ул. Бухарская 4В", "https://near.test/c"),
        AcceptedField("description", "Производим моцареллу", "Производим моцареллу", "https://near.test/")]))
    async with factory() as session:
        result = await run_suppliers(run_id, session)
    names = [s["name"] for s in result["suppliers"]]
    assert names == ["Местный", "Далёкий"]  # местный выше, хотя найден позже
    first = result["suppliers"][0]["ranking"]
    assert first["rank"] == 1 and first["tier"] == "local" and first["recommended"] and sum(first["breakdown"].values()) == first["score"]
    assert result["suppliers"][1]["ranking"]["tier"] == "unknown" and result["suppliers"][1]["ranking"]["missing"]
    assert result["recommended_ids"] == [result["suppliers"][0]["id"]] or len(result["recommended_ids"]) >= 1


@needs_db
@with_db
async def test_supplier_note_is_saved_trimmed_cleared_and_shown_in_run_results(factory):
    from fastapi import HTTPException

    from app.routes.runs import run_suppliers
    from app.routes.suppliers import NoteBody, save_note

    run_id = await new_run(factory)
    outcome = await DbSink(factory, run_id).save(draft())
    async with factory() as session:
        saved = await save_note(outcome.supplier_id, NoteBody(note="  Звонили 25.10, обещали прайс  "), session)
        assert saved["note"] == "Звонили 25.10, обещали прайс"
        assert (await run_suppliers(run_id, session))["suppliers"][0]["note"] == "Звонили 25.10, обещали прайс"
        assert (await save_note(outcome.supplier_id, NoteBody(note="   "), session))["note"] is None  # пустая заметка удаляет
        assert (await run_suppliers(run_id, session))["suppliers"][0]["note"] is None
        with pytest.raises(HTTPException) as error:
            await save_note(999999, NoteBody(note="x"), session)
        assert error.value.status_code == 404


def test_note_length_is_limited():
    from pydantic import ValidationError

    from app.routes.suppliers import MAX_NOTE_LENGTH, NoteBody

    assert NoteBody(note="а" * MAX_NOTE_LENGTH).note
    with pytest.raises(ValidationError):
        NoteBody(note="а" * (MAX_NOTE_LENGTH + 1))


@needs_db
@with_db
async def test_note_survives_a_new_run_that_finds_the_same_supplier(factory):
    from app.routes.suppliers import NoteBody, save_note

    first, second = await new_run(factory), await new_run(factory)
    outcome = await DbSink(factory, first).save(draft())
    async with factory() as session:
        await save_note(outcome.supplier_id, NoteBody(note="Хороший вариант"), session)
    await DbSink(factory, second).save(draft())  # тот же домен найден в другом запуске
    async with factory() as session:
        assert (await session.get(Supplier, outcome.supplier_id)).note == "Хороший вариант"


@needs_db
@with_db
async def test_daily_run_limit_blocks_new_runs_but_not_old_ones(factory, monkeypatch=None):
    from fastapi import HTTPException

    from app.config import Settings
    from app.routes import runs as runs_route
    from app.routes.runs import RunCreate, create_run

    monkeypatch = pytest.MonkeyPatch()
    try:
        started: list[int] = []

        async def fake_start(run_id):
            started.append(run_id)

        monkeypatch.setattr(service, "start_run", fake_start)
        monkeypatch.setattr(runs_route, "get_settings", lambda: Settings(_env_file=None, agent_max_runs_per_day=2))
        first = await new_run(factory)  # уже есть запуск за сутки
        category_id = None
        async with factory() as session:
            category_id = (await session.get(Run, first)).category_id
            await session.execute(update(Run).values(status="done"))
            await session.commit()
            assert await service.runs_last_day(session) == 1
            second = await create_run(RunCreate(category_id=category_id), session)  # второй запуск проходит
            assert started == [second["run_id"]]
            await session.execute(update(Run).values(status="done"))
            await session.commit()
            with pytest.raises(HTTPException) as error:
                await create_run(RunCreate(category_id=category_id), session)  # третий: лимит 2
            assert error.value.status_code == 429 and "суточный лимит" in error.value.detail
            await session.execute(update(Run).values(created_at=datetime.now(UTC) - timedelta(days=2)))
            await session.commit()
            await create_run(RunCreate(category_id=category_id), session)  # старые запуски в лимит не входят
    finally:
        monkeypatch.undo()


@needs_db
@with_db
async def test_research_coverage_is_stored_and_returned_with_run_results(factory):
    from app.routes.runs import run_suppliers

    run_id = await new_run(factory)
    sink = DbSink(factory, run_id)
    await sink.save(draft())
    await sink.record_research("syrmaster-kazan.test", ["https://syrmaster-kazan.test/", "https://syrmaster-kazan.test/contacts"], ["moq", "certificates"])
    await sink.record_research("unknown.test", ["https://unknown.test/"], [])  # неизвестного поставщика пропускаем молча
    async with factory() as session:
        supplier = (await run_suppliers(run_id, session))["suppliers"][0]
    assert supplier["research"]["missing"] == ["moq", "certificates"] and len(supplier["research"]["pages"]) == 2
    assert supplier["research"]["researched_at"]
