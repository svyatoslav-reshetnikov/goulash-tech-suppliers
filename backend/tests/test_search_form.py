"""Search form backend: free-text products, several products as grouped searches, optional requirements."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.agent.config import Limits
from app.agent.models import RunRequest
from app.agent.prompts import build_messages
from app.config import Settings
from app.db.models import Category, Run
from app.extras import Extras, check, describe
from app.ranking import FieldInfo, RankContext, SupplierInfo, rank_suppliers
from app.routes import runs as runs_route
from app.routes.runs import Product, RunBatch, RunCreate, clean_product_text, create_run, create_runs, group_runs, previous_run
from app.runs import service
from tests.test_runs import needs_db, with_db

# --- free-text product ---


def test_free_text_product_is_sanitized_and_length_checked():
    assert clean_product_text("  Рис   «Камолино» (25 кг)  ") == "Рис «Камолино» (25 кг)"
    assert clean_product_text("Нори <script>alert(1)</script>") == "Нори script alert(1) /script"  # markup characters become spaces
    assert clean_product_text("a\x00b\x07c d") == "a b c d"
    for bad in ("", " ", "я", "x" * 81, "<<<>>>"):
        with pytest.raises(HTTPException) as error:
            clean_product_text(bad)
        assert error.value.status_code == 422
    assert clean_product_text("яя") == "яя" and len(clean_product_text("я" * 80)) == 80


def test_agent_prompt_handles_a_free_text_product_without_catalog_data():
    messages = build_messages(RunRequest(category="Мука пшеничная в/с", synonyms=(), regions=()), Limits(), places_enabled=False)
    assert "Продукт для закупки: Мука пшеничная в/с." in messages[1]["content"] and "Другие названия" not in messages[1]["content"]


# --- optional requirements ---


def test_extras_are_normalized_described_and_checked_without_llm():
    assert Extras().as_dict() is None  # nothing filled in: nothing stored
    extras = Extras(volume_value=500, volume_unit="кг", documents=["haccp", "haccp", "mercury"], storage="chilled", delivery="  до 8:00 \n по будням ").as_dict()
    assert extras == {"volume": {"value": 500, "unit": "кг"}, "documents": ["haccp", "mercury"], "storage": "chilled", "delivery": "до 8:00 по будням"}
    assert describe(extras) == ["объём закупки около 500 кг в месяц", "нужны документы: ХАССП, ветеринарные документы (Меркурий)",
                                "нужно охлаждённое хранение", "доставка: до 8:00 по будням"]
    missing, questions = check(extras, ["Сертификат ХАССП", "Доставка по Москве"])
    assert missing == ["ветеринарные документы (Меркурий)", "охлаждённое хранение"]
    assert questions[0].startswith("Уточните, есть ли ветеринарные") and any("500 кг" in q for q in questions) and any("до 8:00" in q for q in questions)
    assert check(extras, ["ХАССП, Меркурий, хранение при +2°С"])[0] == []
    assert check(None, ["x"]) == ([], [])
    with pytest.raises(ValueError):
        Extras(documents=["nope"])  # type: ignore[list-item]


def test_extras_requirements_become_warnings_and_the_first_questions():
    ctx = RankContext(category="Рис для суши", synonyms=("Рис",), extras={"documents": ["haccp", "iso"], "delivery": "до 8:00"})
    info = SupplierInfo(id=1, domain="s.test", name="С", inn=None, pages_studied=5,
                        fields={"assortment": [FieldInfo("Рис для суши")], "certificates": [FieldInfo("HACCP")], "phone": [FieldInfo("+7 (495) 111-22-33")]})
    ranked = rank_suppliers([info], ctx)[0]
    texts = [w["text"] for w in ranked.warnings if w["code"] == "extra_not_found"]
    assert texts == ["Не найдено на сайте: ISO"]
    assert ranked.questions[0] == "Уточните, есть ли ISO." and any("до 8:00" in q for q in ranked.questions)
    assert not [w for w in rank_suppliers([info], RankContext(category="Рис для суши", synonyms=("Рис",)))[0].warnings if w["code"] == "extra_not_found"]  # no extras: no checks


def test_agent_prompt_lists_the_requirements():
    request = RunRequest(category="Лосось", extras={"documents": ["haccp"], "storage": "frozen"})
    text = build_messages(request, Limits(), places_enabled=False)[1]["content"]
    assert "Дополнительные требования закупщика: нужны документы: ХАССП; нужно замороженное хранение." in text and "не отбрасывай" in text


# --- searches on a real database ---


async def _start_recorder(monkeypatch) -> list[int]:
    started: list[int] = []

    async def fake_start(run_id):
        started.append(run_id)

    monkeypatch.setattr(service, "start_run", fake_start)
    monkeypatch.setattr(runs_route, "get_settings", lambda: Settings(_env_file=None, agent_max_runs_per_day=10, agent_max_products_per_search=3))
    return started


async def _categories(factory, *names: str) -> list[int]:
    async with factory() as session:
        ids = []
        for name in names:
            category = Category(name=name, normalized_name=name.lower(), synonyms=[], origin="derived")
            session.add(category)
            await session.flush()
            ids.append(category.id)
        await session.commit()
        return ids


@needs_db
@with_db
async def test_free_text_search_is_stored_without_a_category_and_requirements_are_kept(factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        started = await _start_recorder(monkeypatch)
        async with factory() as session:
            body = RunCreate(product_text=" Рис   «Камолино» ", extras=Extras(documents=["haccp"], storage="frozen"))
            run_id = (await create_run(body, session))["run_id"]
            run = await session.get(Run, run_id)
            assert run.category_id is None and run.category_name == "Рис «Камолино»" and run.group_id is None
            assert run.extras == {"documents": ["haccp"], "storage": "frozen"} and started == [run_id]
            found = (await previous_run(category_id=None, product_text="Рис «Камолино»", location_kind=None, location_key=None, include_neighbors=False, supplier_type=None, session=session))["run"]
            assert found["id"] == run_id and found["free_text"] is True
            with pytest.raises(HTTPException) as error:
                await create_run(RunCreate(product_text="я"), session)
            assert error.value.status_code == 422
            with pytest.raises(HTTPException) as error:
                await create_run(RunCreate(), session)
            assert error.value.status_code == 422
    finally:
        monkeypatch.undo()


@needs_db
@with_db
async def test_several_products_become_grouped_searches_started_one_by_one(factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        started = await _start_recorder(monkeypatch)
        first, second = await _categories(factory, "Нори", "Лосось")
        async with factory() as session:
            body = RunBatch(products=[Product(category_id=first), Product(category_id=second), Product(product_text="Угорь копчёный")], supplier_type="оптовик")
            result = await create_runs(body, session)
            ids = result["run_ids"]
            assert len(ids) == 3 and result["group_id"] == ids[0] and started == [ids[0]]  # only the first one starts
            runs = (await session.execute(select(Run).order_by(Run.id))).scalars().all()
            assert [r.group_id for r in runs] == [ids[0]] * 3 and [r.status for r in runs] == ["queued"] * 3
            assert [r.category_name for r in runs] == ["Нори", "Лосось", "Угорь копчёный"] and {r.supplier_type for r in runs} == {"оптовик"}
            assert [r["id"] for r in (await group_runs(ids[0], session))["runs"]] == ids

            await session.execute(update(Run).where(Run.id == ids[0]).values(status="done"))
            await session.commit()
            await service.start_next_in_group(factory, ids[0])
            assert started == [ids[0], ids[1]]  # the next one follows when the previous finished
            await session.execute(update(Run).where(Run.id == ids[1]).values(status="running"))
            await session.commit()
            await service.start_next_in_group(factory, ids[0])
            assert started == [ids[0], ids[1]]  # not while another run of the group is still running
            await session.execute(update(Run).where(Run.id == ids[1]).values(status="failed"))
            await session.commit()
            await service.start_next_in_group(factory, ids[1])
            assert started[-1] == ids[2]  # a failed search does not stop the group
            await service.start_next_in_group(factory, ids[2])
            assert len(started) == 3
    finally:
        monkeypatch.undo()


@needs_db
@with_db
async def test_batch_respects_the_product_limit_and_the_daily_limit(factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        await _start_recorder(monkeypatch)
        ids = await _categories(factory, "А", "Б", "В", "Г")
        async with factory() as session:
            with pytest.raises(HTTPException) as error:
                await create_runs(RunBatch(products=[Product(category_id=i) for i in ids]), session)  # 4 > 3
            assert error.value.status_code == 422 and "Не больше 3" in error.value.detail
            monkeypatch.setattr(runs_route, "get_settings", lambda: Settings(_env_file=None, agent_max_runs_per_day=2, agent_max_products_per_search=3))
            with pytest.raises(HTTPException) as error:
                await create_runs(RunBatch(products=[Product(category_id=i) for i in ids[:3]]), session)  # 3 searches would exceed 2 a day
            assert error.value.status_code == 429 and "суточный лимит" in error.value.detail
            assert (await session.execute(select(Run))).first() is None  # nothing half-created
    finally:
        monkeypatch.undo()
