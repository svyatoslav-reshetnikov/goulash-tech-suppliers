"""Клиент языковой модели (Yandex AI Studio, OpenAI-совместимый API). Модель задаётся настройками LLM_*."""

from dataclasses import dataclass, field

from app.config import Settings, get_settings


class LLMTruncated(ValueError):
    """Ответ модели обрезан по длине: JSON получился неполным. Токены обрезанного ответа тоже оплачены и учитываются."""

    def __init__(self, message: str = "ответ модели обрезан по длине", prompt_tokens: int = 0, completion_tokens: int = 0):
        super().__init__(message)
        self.prompt_tokens, self.completion_tokens = prompt_tokens, completion_tokens


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


class LLM:
    def __init__(self, settings: Settings):
        settings.require("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "YANDEX_CLOUD_FOLDER_ID")
        from openai import AsyncOpenAI

        folder = settings.yandex_cloud_folder_id
        model = settings.llm_model
        self.model_name = model
        self._model = model if model.startswith("gpt://") else f"gpt://{folder}/{model}"
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            project=folder,
            timeout=120,
        )

    async def complete(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 4096) -> str:
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMTruncated("ответ модели обрезан по длине")
        return choice.message.content or ""

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.0, max_tokens: int = 2048
    ) -> Reply:
        """Один ход диалога с возможностью вызова инструментов (нативные tool calls)."""
        kwargs = {"tools": tools} if tools else {}
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages, temperature=temperature, max_tokens=max_tokens, **kwargs
        )
        choice = response.choices[0]
        usage = response.usage
        if choice.finish_reason == "length":
            raise LLMTruncated(prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                               completion_tokens=getattr(usage, "completion_tokens", 0) or 0)
        return Reply(
            text=choice.message.content or None,
            tool_calls=[
                ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
                for c in (choice.message.tool_calls or [])
            ],
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def get_llm() -> LLM:
    return LLM(get_settings())
