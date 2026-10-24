"""Клиенты моделей. У всех один интерфейс: chat(messages, tools) -> Reply."""

import json
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

from app.config import ENV_FILE, Settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # сырая JSON-строка, как вернула модель


@dataclass
class Reply:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


class ModelClient(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply: ...


class YandexClient:
    """Yandex AI Studio через OpenAI-совместимый API (Chat Completions)."""

    def __init__(self, settings: Settings):
        settings.require("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "YANDEX_CLOUD_FOLDER_ID")
        from openai import OpenAI

        folder = settings.yandex_cloud_folder_id
        model = settings.llm_model
        self._model = model if model.startswith("gpt://") else f"gpt://{folder}/{model}"
        self.name = f"yandex:{model}"
        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            project=folder,
            timeout=120,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        kwargs = {"tools": tools} if tools else {}
        started = time.perf_counter()
        response = self._client.chat.completions.create(
            model=self._model, messages=messages, temperature=0.1, **kwargs
        )
        seconds = time.perf_counter() - started
        message = response.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
            for c in (message.tool_calls or [])
        ]
        usage = response.usage
        return Reply(
            text=message.content,
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            seconds=seconds,
        )


def _as_object(arguments):
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_json_text(content: str) -> str:
    """GigaChat ждёт результат функции в виде JSON; обычный текст оборачиваем."""
    try:
        json.loads(content)
        return content
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"result": content}, ensure_ascii=False)


def to_gigachat_messages(messages: list[dict]) -> list[dict]:
    """Переводит сообщения из формата OpenAI (tool_calls / role=tool) в формат GigaChat (function_call / role=function).

    GigaChat возвращает за ход один вызов функции, поэтому в assistant-сообщении берём первый.
    """
    names: dict[str, str] = {}
    out: list[dict] = []
    for message in messages:
        role = message["role"]
        if role == "assistant" and message.get("tool_calls"):
            call = message["tool_calls"][0]
            names[call["id"]] = call["function"]["name"]
            out.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "function_call": {
                        "name": call["function"]["name"],
                        "arguments": _as_object(call["function"]["arguments"]),
                    },
                }
            )
        elif role == "tool":
            out.append(
                {
                    "role": "function",
                    "name": names.get(message["tool_call_id"], ""),
                    "content": _as_json_text(message["content"]),
                }
            )
        else:
            out.append({"role": role, "content": message["content"]})
    return out


def parse_gigachat_reply(data: dict, seconds: float) -> Reply:
    message = data["choices"][0]["message"]
    calls = []
    function_call = message.get("function_call")
    if function_call:
        arguments = function_call.get("arguments", {})
        calls.append(
            ToolCall(
                id=f"call-{uuid.uuid4().hex[:8]}",
                name=function_call["name"],
                arguments=arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False),
            )
        )
    usage = data.get("usage") or {}
    return Reply(
        text=message.get("content") or None,
        tool_calls=calls,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        seconds=seconds,
    )


def _ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    if not ca_bundle:
        return ssl.create_default_context()
    path = Path(ca_bundle)
    if not path.is_absolute():
        path = ENV_FILE.parent / path
    return ssl.create_default_context(cafile=str(path))


class GigaChatClient:
    """GigaChat API (Сбер): токен по ключу авторизации, вызов функций через поле functions."""

    def __init__(self, settings: Settings):
        settings.require("GIGACHAT_CREDENTIALS")
        self._credentials = settings.gigachat_credentials.get_secret_value()
        self._scope = settings.gigachat_scope
        self._model = settings.gigachat_model
        self._auth_url = settings.gigachat_auth_url
        self._base_url = settings.gigachat_base_url.rstrip("/")
        self._ssl = _ssl_context(settings.gigachat_ca_bundle)
        self._token: str | None = None
        self._expires_at = 0.0
        self.name = f"gigachat:{self._model}"

    def _post(self, url: str, body: bytes, headers: dict) -> dict:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120, context=self._ssl) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from None

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        data = self._post(
            self._auth_url,
            urlencode({"scope": self._scope}).encode(),
            {
                "Authorization": f"Basic {self._credentials}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        self._token = data["access_token"]
        self._expires_at = data.get("expires_at", 0) / 1000  # в ответе миллисекунды
        return self._token

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        body: dict = {"model": self._model, "messages": to_gigachat_messages(messages), "temperature": 0.1}
        if tools:
            body["functions"] = [tool["function"] for tool in tools]
            body["function_call"] = "auto"
        token = self._access_token()
        started = time.perf_counter()
        data = self._post(
            f"{self._base_url}/chat/completions",
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        return parse_gigachat_reply(data, time.perf_counter() - started)


def get_client(provider: str, settings: Settings) -> ModelClient:
    if provider == "yandex":
        return YandexClient(settings)
    if provider == "gigachat":
        return GigaChatClient(settings)
    raise ValueError(f"Неизвестный провайдер: {provider}")
