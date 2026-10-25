"""Цикл агента: модель выбирает инструмент, код выполняет его и проверяет результат.

Остановка наступает по одной из причин: finished (модель завершила), target_reached (набрано нужное число
поставщиков), turn_limit, time_limit, token_limit (бюджет), no_action (модель перестала вызывать инструменты)
и llm_error. Бюджеты заданы в коде, а не в промпте: модель их обойти не может.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.agent.config import Limits
from app.agent.models import RunRequest
from app.agent.prompts import build_messages
from app.agent.tools import ToolBox
from app.llm import LLMTruncated

log = logging.getLogger(__name__)

Emit = Callable[..., Awaitable[None]]


class ChatReply(Protocol):
    text: str | None
    tool_calls: list
    prompt_tokens: int
    completion_tokens: int


class ChatModel(Protocol):
    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatReply: ...


@dataclass
class AgentResult:
    stop_reason: str
    summary: str | None
    stats: dict = field(default_factory=dict)
    error: str | None = None


_TEXT_CALL = re.compile(r"\[TOOL_CALL_START\]\s*([A-Za-z_]+)\s*(\{.*?\})\s*(?=\[TOOL_CALL_START\]|\[TOOL_CALL_END\]|$)", re.S)


@dataclass
class _TextCall:
    id: str
    name: str
    arguments: str


def parse_text_tool_calls(text: str | None) -> list[_TextCall]:
    """YandexGPT иногда пишет вызов инструмента обычным текстом: «[TOOL_CALL_START]имя {json}» вместо служебного поля.

    Такой вызов разбирается так же, как настоящий. Если JSON после имени битый, вызов игнорируется.
    """
    calls = []
    for name, raw in _TEXT_CALL.findall(text or ""):
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            continue
        calls.append(_TextCall(id=f"text-{uuid.uuid4().hex[:8]}", name=name, arguments=raw))
    return calls


def compact(messages: list[dict], keep: int) -> None:
    """Сжимает старые результаты fetch_page (самые длинные), чтобы контекст не рос с каждым шагом.

    Результаты поиска и сохранения остаются целиком: по ним модель выбирает следующие адреса.
    Полный текст страниц хранится у инструментов и проверяется по нему, а страницу можно открыть заново из кэша.
    """
    fetches = [i for i, m in enumerate(messages) if m["role"] == "tool" and m.get("name") == "fetch_page"]
    for i in fetches[:-keep] if keep else fetches:
        content = messages[i]["content"]
        if len(content) > 400:
            try:
                data = json.loads(content)
                brief = {"адрес": data.get("адрес"), "заголовок": data.get("заголовок"),
                         "примечание": "текст страницы сокращён; открой страницу снова, если нужен текст (из кэша, без нагрузки)"}
                messages[i] = {**messages[i], "content": json.dumps(brief, ensure_ascii=False)}
            except json.JSONDecodeError:
                messages[i] = {**messages[i], "content": content[:300] + " …[сокращено]"}


async def run_agent(request: RunRequest, llm: ChatModel, toolbox: ToolBox, emit: Emit, limits: Limits) -> AgentResult:
    messages = build_messages(request, limits, places_enabled=toolbox.places is not None)
    started = time.monotonic()
    prompt_tokens = completion_tokens = turns = 0
    stop_reason, error = "turn_limit", None

    def stats() -> dict:
        return {
            "turns": turns,
            "tool_calls": toolbox.counters.tool_calls,
            "searches": toolbox.counters.searches,
            "fetches": toolbox.counters.fetches,
            "suppliers_saved": toolbox.counters.saved,
            "researched": toolbox.counters.researched,
            "site_searches": toolbox.counters.site_searches,
            "prompt_tokens": prompt_tokens + toolbox.extra_prompt_tokens,
            "completion_tokens": completion_tokens + toolbox.extra_completion_tokens,
            "seconds": round(time.monotonic() - started, 1),
        }

    await emit("run_started", "Запуск агента", category=request.category,
               regions=[r.name for r in request.regions], limits={"turns": limits.max_turns, "seconds": limits.max_seconds})

    while turns < limits.max_turns:
        if time.monotonic() - started > limits.max_seconds:
            stop_reason = "time_limit"
            break
        if prompt_tokens + completion_tokens + toolbox.extra_prompt_tokens + toolbox.extra_completion_tokens > limits.max_total_tokens:
            stop_reason = "token_limit"
            break

        compact(messages, limits.keep_recent_tool_results)
        try:
            reply = await llm.chat(messages, toolbox.schemas())
        except LLMTruncated as exc:  # оплаченный, но обрезанный ответ: токены учитываем, найденное сохраняется
            prompt_tokens += exc.prompt_tokens
            completion_tokens += exc.completion_tokens
            stop_reason, error = "llm_error", "ответ модели обрезан по длине"
            break
        except Exception as exc:
            log.exception("Ошибка модели")
            stop_reason, error = "llm_error", str(exc)[:300]
            break
        turns += 1
        prompt_tokens += reply.prompt_tokens
        completion_tokens += reply.completion_tokens
        toolbox.loop_prompt_tokens, toolbox.loop_completion_tokens = prompt_tokens, completion_tokens
        await toolbox.emit_usage()

        calls = list(reply.tool_calls) or parse_text_tool_calls(reply.text)
        if not calls:  # модель ответила текстом без вызова: считаем это итогом
            stop_reason = "no_action"
            toolbox.summary = toolbox.summary or (reply.text or "").strip() or None
            break
        if not reply.tool_calls:
            await emit("note", "Модель написала вызов инструмента текстом: разобран как обычный вызов")

        messages.append(
            {
                "role": "assistant",
                "content": reply.text if reply.tool_calls else None,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in calls
                ],
            }
        )
        for call in calls:
            result = await toolbox.execute(call.name, call.arguments, turns_left=limits.max_turns - turns)
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": result.text})

        if toolbox.finished:
            stop_reason = "finished"
            break
        if toolbox.counters.saved >= request.target:
            stop_reason = "target_reached"
            break

    summary = toolbox.summary or f"Сохранено поставщиков: {toolbox.counters.saved}"
    await emit("run_finished", f"Готово: сохранено поставщиков {toolbox.counters.saved} ({stop_reason})",
               stop_reason=stop_reason, stats=stats())
    return AgentResult(stop_reason=stop_reason, summary=summary, stats=stats(), error=error)
