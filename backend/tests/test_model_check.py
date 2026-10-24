"""Проверяем сам скрипт проверки моделей: оценки и цикл с инструментами (без обращения к API)."""

import json

from scripts.model_check import cases
from scripts.model_check.clients import Reply, ToolCall

PAGE = cases.load_page()

GOOD_EXTRACTION = {
    "name": {"value": "ООО «СырМастер Казань»", "quote": "ООО «СырМастер Казань»"},
    "website": {"value": "https://syrmaster-kazan.test", "quote": "Сайт: https://syrmaster-kazan.test"},
    "phone": {"value": "+7 (843) 555-01-23", "quote": "Телефон: +7 (843) 555-01-23"},
    "email": {"value": "sales@syrmaster-kazan.test", "quote": "Email: sales@syrmaster-kazan.test"},
    "inn": {"value": "1655012345", "quote": "ИНН 1655012345"},
    "moq": {"value": "от 20 кг", "quote": "Минимальный заказ — от 20 кг."},
    "price_hint": {"value": "от 520 руб/кг", "quote": "Моцарелла для пиццы 45%, брусок 2,5 кг — от 520 руб/кг"},
    "delivery_terms": {
        "value": "бесплатно от 30 000 руб.",
        "quote": "Доставка по Казани и Республике Татарстан — бесплатно при заказе от 30 000 руб.",
    },
    "regions": {"value": "Казань, Татарстан", "quote": "Работаем с пиццериями, суши-барами и ресторанами Татарстана."},
    "certificates": None,
}


class ScriptedClient:
    """Отдаёт заранее заготовленные ответы по очереди."""

    name = "scripted"

    def __init__(self, replies: list[Reply]):
        self._replies = list(replies)

    def chat(self, messages, tools=None) -> Reply:
        return self._replies.pop(0)


def call(tool: str, **args) -> Reply:
    return Reply(text=None, tool_calls=[ToolCall(id=f"c-{tool}", name=tool, arguments=json.dumps(args, ensure_ascii=False))])


GOOD_SAVE = dict(
    name="СырМастер Казань",
    website="https://syrmaster-kazan.test",
    phone="+7 (843) 555-01-23",
    phone_quote="Телефон: +7 (843) 555-01-23",
)


def test_extract_json_handles_code_fence():
    assert cases.extract_json('Вот ответ:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert cases.extract_json("без json") is None


def test_extraction_perfect_answer_scores_one():
    score, notes = cases.score_extraction(GOOD_EXTRACTION, PAGE)
    assert score == 1.0 and notes == []


def test_extraction_penalizes_invented_field_and_fake_quote():
    data = dict(GOOD_EXTRACTION)
    data["certificates"] = {"value": "ISO 22000", "quote": "Сертификат ISO 22000"}
    data["phone"] = {"value": "+7 (843) 555-01-23", "quote": "Звоните: 555-01-23"}
    score, notes = cases.score_extraction(data, PAGE)
    assert score == 0.85  # -1 за выдуманное поле, -0.5 за ненайденную цитату из 10
    assert "выдумал certificates" in notes
    assert "цитата phone не найдена на странице" in notes


def test_extraction_null_value_object_means_not_found():
    data = dict(GOOD_EXTRACTION)
    data["certificates"] = {"value": None, "quote": None}
    score, notes = cases.score_extraction(data, PAGE)
    assert score == 1.0 and notes == []


def test_ingredients_perfect_and_hallucinated():
    dishes = cases.load_dishes()
    good = {"items": [{"id": 1, "ingredients": ["лосось", "сливочный сыр", "рис", "нори"]},
                      {"id": 2, "ingredients": ["курица", "салат айсберг", "помидор", "огурец", "сливочный сыр", "тортилья"]},
                      {"id": 3, "ingredients": ["авокадо", "рис", "нори", "кунжут"]},
                      {"id": 4, "ingredients": ["огурец", "нори", "рис"]},
                      {"id": 5, "ingredients": ["рис", "кунжут"]}]}
    score, notes = cases.score_ingredients(good, dishes)
    assert score == 1.0 and notes == []

    bad = json.loads(json.dumps(good))
    bad["items"][0]["ingredients"] += ["соевый соус", "васаби"]
    score, notes = cases.score_ingredients(bad, dishes)
    assert score < 1.0
    assert "лишнее в блюде 1: соевый соус" in notes


def test_tool_loop_success():
    client = ScriptedClient([
        call("search_web", query="моцарелла Казань опт", region="Казань"),
        call("fetch_page", url=cases.PAGE_URL),
        call("save_supplier", **GOOD_SAVE),
        call("finish", summary="готово"),
    ])
    result = cases.tool_calling(client)
    assert result.passed and result.score == 1.0


def test_tool_loop_rejects_fake_quote_then_recovers():
    client = ScriptedClient([
        call("search_web", query="моцарелла Казань"),
        call("fetch_page", url=cases.PAGE_URL),
        call("save_supplier", **{**GOOD_SAVE, "phone_quote": "Звоните нам: 555-01-23"}),
        call("save_supplier", **GOOD_SAVE),
        call("finish", summary="готово"),
    ])
    assert cases.tool_calling(client).passed


def test_tool_loop_fails_when_model_gives_up_or_saves_wrong_supplier():
    bad_save = {**GOOD_SAVE, "website": "https://marketplace-example.test/mozzarella"}
    client = ScriptedClient([
        call("search_web", query="моцарелла"),
        call("save_supplier", **bad_save),
        Reply(text="Не получилось."),
    ])
    result = cases.tool_calling(client)
    assert not result.passed
    assert any("не сумел сохранить" in n for n in result.notes)


def test_tool_loop_counts_invalid_arguments_and_loops():
    client = ScriptedClient([Reply(text=None, tool_calls=[ToolCall("1", "fetch_page", '{"wrong": 1}')])] * 3
                            + [Reply(text="конец")])
    result = cases.tool_calling(client)
    assert not result.passed
    assert any("некорректных вызовов: 3" in n for n in result.notes)
    assert any("зациклился" in n for n in result.notes)


def test_tool_loop_json_mode():
    def json_reply(tool, **args):
        return Reply(text=json.dumps({"tool": tool, "args": args}, ensure_ascii=False))

    client = ScriptedClient([
        json_reply("search_web", query="моцарелла Казань"),
        json_reply("fetch_page", url=cases.PAGE_URL),
        json_reply("save_supplier", **GOOD_SAVE),
        json_reply("finish", summary="готово"),
    ])
    assert cases.tool_calling(client, mode="json").passed


def test_api_error_is_reported_not_raised():
    class Broken:
        name = "broken"

        def chat(self, messages, tools=None):
            raise RuntimeError("tools не поддерживаются")

    result = cases.tool_calling(Broken())
    assert not result.passed and "tools не поддерживаются" in result.error


# --- GigaChat: перевод формата сообщений и разбор ответа (без сети) ---

from pydantic import SecretStr  # noqa: E402

from app.config import Settings  # noqa: E402
from scripts.model_check.clients import (  # noqa: E402
    GigaChatClient,
    parse_gigachat_reply,
    to_gigachat_messages,
)


def test_gigachat_message_translation():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "fetch_page", "arguments": '{"url": "https://x.test"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Текст страницы"},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
    ]
    out = to_gigachat_messages(messages)
    assert out[2] == {"role": "assistant", "content": "",
                      "function_call": {"name": "fetch_page", "arguments": {"url": "https://x.test"}}}
    assert out[3] == {"role": "function", "name": "fetch_page",
                      "content": json.dumps({"result": "Текст страницы"}, ensure_ascii=False)}
    assert out[4]["content"] == '{"ok": true}'


def test_gigachat_reply_with_function_call_and_object_arguments():
    data = {"choices": [{"message": {"role": "assistant", "content": "",
                                     "function_call": {"name": "search_web", "arguments": {"query": "сыр"}}},
                         "finish_reason": "function_call"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    reply = parse_gigachat_reply(data, 1.5)
    assert reply.text is None and reply.prompt_tokens == 10
    assert reply.tool_calls[0].name == "search_web"
    assert json.loads(reply.tool_calls[0].arguments) == {"query": "сыр"}


def test_gigachat_token_is_cached_and_refreshed(monkeypatch):
    import time as _time

    settings = Settings(_env_file=None, gigachat_credentials=SecretStr("abc"))
    client = GigaChatClient(settings)
    calls = []

    def fake_post(url, body, headers):
        calls.append(url)
        if "oauth" in url:
            return {"access_token": f"t{len(calls)}", "expires_at": (_time.time() + 1800) * 1000}
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(client, "_post", fake_post)
    client.chat([{"role": "user", "content": "hi"}])
    client.chat([{"role": "user", "content": "hi"}])
    assert sum("oauth" in u for u in calls) == 1  # токен переиспользуется
    client._expires_at = 0
    client.chat([{"role": "user", "content": "hi"}])
    assert sum("oauth" in u for u in calls) == 2  # истёк, получен заново


def test_gigachat_requires_credentials():
    import pytest

    from app.config import MissingSettingError

    with pytest.raises(MissingSettingError):
        GigaChatClient(Settings(_env_file=None))
