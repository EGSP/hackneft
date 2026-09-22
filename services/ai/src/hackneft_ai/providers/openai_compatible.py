"""Провайдер с OpenAI-совместимым API.

Yandex AI Studio и локальные серверы моделей (vLLM, Ollama, llama.cpp) принимают один и тот же
формат Chat Completions, поэтому реализация одна, а различия — адрес, аутентификация, форма
идентификатора модели, подсказки к отказам — передаются параметрами.

Ядро объявляет собственные типы сообщений и не знает о формате провайдера; преобразование
выполняется здесь.
"""

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx2
import openai
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from hackneft_common.ai import ProviderModel

from ..core.errors import ModelFailure
from ..core.messages import (
    AgentMessage,
    AgentToolCall,
    AssistantMessage,
    ModelReply,
    SystemMessage,
    TokenUsage,
    ToolMessage,
    UserMessage,
)
from ..core.tool import ToolSpec
from .base import ChatSettings, ProviderModelList, ProviderModelsFailed, ProviderModelsOk
from .credentials import Credentials
from .retry import describe_error_chain, with_retry
from .yandex_iam import YandexAuthError

_MODELS_CACHE_TTL_S = 30
"""Сколько перечень моделей считается свежим. Меньше интервала проверки доступности."""


def _same(identifier: str) -> str:
    return identifier


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        credentials: Credentials,
        http: httpx2.AsyncClient,
        resolve_model: Callable[[str], str] = _same,
        short_name: Callable[[str], str] = _same,
        list_headers: Mapping[str, str] | None = None,
        project: str | None = None,
        status_hints: Mapping[int, str] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url
        self._credentials = credentials
        self._http = http
        self._resolve_model = resolve_model
        self._short_name = short_name
        self._list_headers = dict(list_headers or {})
        self._status_hints = dict(status_hints or {})
        # Ключ в конструктор клиента не зашивается: `api_key` — обязательный заполнитель, а
        # авторизация подставляется заголовком на каждый запрос. Повторы выполняет сервис: он
        # повторяет только отказы соединения.
        self._client = openai.AsyncOpenAI(
            api_key="per-request", base_url=base_url, project=project, max_retries=0
        )
        self._models_cache: tuple[ProviderModelList, float] | None = None

    def model_uri(self, identifier: str) -> str:
        return self._resolve_model(identifier)

    async def complete(
        self,
        identifier: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[ToolSpec],
        settings: ChatSettings,
        *,
        result_schema: Mapping[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> ModelReply:
        # Формат ответа по схеме — структурированный вывод Chat Completions. Строгий режим
        # не включается: он требует от схемы ограничений, которых вызывающая сторона может
        # не соблюдать, а соответствие схеме сервис всё равно проверяет сам.
        response_format: Any = (
            openai.omit
            if result_schema is None
            else {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": dict(result_schema)},
            }
        )
        try:
            authorization = await self._credentials.authorization()
            response = await with_retry(
                lambda: self._client.chat.completions.create(
                    model=self.model_uri(identifier),
                    messages=[_provider_message(message) for message in messages],
                    temperature=settings.temperature,
                    max_tokens=openai.omit if settings.max_tokens is None else settings.max_tokens,
                    tools=[_provider_tool(spec) for spec in tools] if tools else openai.omit,
                    response_format=response_format,
                    reasoning_effort=openai.omit if reasoning_effort is None else reasoning_effort,  # type: ignore[arg-type]
                    # Без учётных данных заголовок снимается совсем, а не несёт заполнитель.
                    extra_headers={
                        "Authorization": openai.omit if authorization is None else authorization
                    },
                )
            )
        except (openai.OpenAIError, httpx2.HTTPError, YandexAuthError) as error:
            raise ModelFailure(self._describe_failure(error)) from error

        if not response.choices:
            raise ModelFailure("Модель вернула ответ без вариантов (choices пуст)")
        choice = response.choices[0]
        usage = response.usage
        return ModelReply(
            content=choice.message.content or "",
            tool_calls=_tool_calls(choice.message),
            reasoning=_reasoning(choice.message),
            finish_reason=choice.finish_reason or None,
            usage=TokenUsage(
                prompt=usage.prompt_tokens if usage is not None else 0,
                completion=usage.completion_tokens if usage is not None else 0,
            ),
        )

    async def list_models(self, *, force: bool = False) -> ProviderModelList:
        """Перечень моделей по стандартному пути `GET /models`.

        Ответ содержит только идентификатор и владельца: сведений о поддержке вызова
        инструментов и режима рассуждения спецификация не предусматривает, поэтому такие
        признаки в справочнике проставляются вручную.
        """
        cached = self._models_cache
        if not force and cached is not None and time.monotonic() - cached[1] < _MODELS_CACHE_TTL_S:
            return cached[0]
        listing = await self._fetch_models()
        self._models_cache = (listing, time.monotonic())
        return listing

    async def aclose(self) -> None:
        await self._client.close()

    async def _fetch_models(self) -> ProviderModelList:
        try:
            authorization = await self._credentials.authorization()
            headers = dict(self._list_headers)
            if authorization is not None:
                headers["Authorization"] = authorization
            response = await with_retry(
                lambda: self._http.get(f"{self.base_url}/models", headers=headers, timeout=20)
            )
        except YandexAuthError as error:
            return ProviderModelsFailed(
                f"Аутентификация в Yandex Cloud не удалась: {error}", error.kind
            )
        except httpx2.HTTPError as error:
            return ProviderModelsFailed(
                f"Не удалось обратиться к провайдеру: {describe_error_chain(error)}", "unreachable"
            )

        if response.status_code >= 400:
            # Отказ с кодом 4xx означает неверные секреты, каталог или адрес — это исправляется
            # правкой карточки; отказ 5xx говорит о неисправности на стороне провайдера.
            hint = self._status_hints.get(response.status_code, "")
            return ProviderModelsFailed(
                f"Перечень моделей недоступен: HTTP {response.status_code}. "
                f"{response.text[:200]}{' ' + hint if hint else ''}".strip(),
                "unreachable" if response.status_code >= 500 else "unsatisfied",
            )
        try:
            payload = response.json()
        except ValueError:
            return ProviderModelsFailed(
                "Перечень моделей не разобран: ответ не является JSON. Проверьте адрес API.",
                "unsatisfied",
            )

        entries = payload.get("data") if isinstance(payload, dict) else None
        models = tuple(
            ProviderModel(
                identifier=self._short_name(entry["id"]),
                full_id=entry["id"],
                vendor=entry["owned_by"]
                if isinstance(entry.get("owned_by"), str)
                else "неизвестно",
            )
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        )
        return ProviderModelsOk(models, datetime.now().astimezone())

    def _describe_failure(self, error: BaseException) -> str:
        """Ответы провайдера на неверную модель или недостаточные права содержательны, но
        теряются внутри объекта ошибки."""
        if isinstance(error, openai.APIStatusError):
            details = (
                json.dumps(error.body, ensure_ascii=False)
                if error.body is not None
                else error.message
            )
            hint = self._status_hints.get(error.status_code, "")
            return (
                f"Обращение к модели отклонено: HTTP {error.status_code}. {details}"
                f"{' ' + hint if hint else ''}"
            )
        if isinstance(error, YandexAuthError):
            return f"Аутентификация в Yandex Cloud не удалась: {error}"
        return f"Обращение к модели не удалось: {describe_error_chain(error)}"


def _provider_message(message: AgentMessage) -> ChatCompletionMessageParam:
    match message:
        case SystemMessage():
            return {"role": "system", "content": message.content}
        case UserMessage():
            return {"role": "user", "content": message.content}
        case ToolMessage():
            return {"role": "tool", "tool_call_id": message.call_id, "content": message.content}
        case AssistantMessage():
            if not message.tool_calls:
                return {"role": "assistant", "content": message.content}
            return {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.raw_arguments},
                    }
                    for call in message.tool_calls
                ],
            }


def _provider_tool(spec: ToolSpec) -> ChatCompletionToolParam:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _tool_calls(message: ChatCompletionMessage) -> tuple[AgentToolCall, ...]:
    calls = []
    for call in message.tool_calls or []:
        if not isinstance(call, ChatCompletionMessageFunctionToolCall):
            continue
        calls.append(
            AgentToolCall(
                # Часть локальных серверов не присылает идентификатор. Он нужен только для
                # связи вызова с результатом внутри журнала, поэтому подставляется свой.
                id=call.id or f"call_{uuid.uuid4().hex[:12]}",
                name=call.function.name,
                raw_arguments=call.function.arguments or "",
            )
        )
    return tuple(calls)


def _reasoning(message: ChatCompletionMessage) -> str | None:
    """Текст рассуждения.

    Стандартом OpenAI он не описан. Yandex AI Studio, DeepSeek и развёртывания Qwen в vLLM
    возвращают поле `reasoning_content`, Ollama и новые версии vLLM — `reasoning`.
    """
    extra = message.model_extra or {}
    for key in ("reasoning_content", "reasoning"):
        value = extra.get(key)
        if isinstance(value, str) and value != "":
            return value
    return None
