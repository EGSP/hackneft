"""Провайдер модели.

Провайдер — источник моделей: он выполняет обращение к модели и сообщает перечень доступных
моделей. Всё, что отличает один провайдер от другого, — адрес, способ аутентификации, форма
идентификатора модели, имя поля рассуждения, — находится внутри реализации. Ядро видит только
зависимость `ModelClient`, которую сервис собирает из провайдера и модели хода.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from hackneft_common.ai import ProviderModel

from ..core.messages import AgentMessage, ModelReply
from ..core.tool import ToolSpec


@dataclass(frozen=True, slots=True)
class ChatSettings:
    """Параметры обращения, общие для всех моделей сервиса."""

    temperature: float
    max_tokens: int | None


@dataclass(frozen=True, slots=True)
class ProviderModelsOk:
    models: tuple[ProviderModel, ...]
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderModelsFailed:
    error: str
    kind: Literal["unsatisfied", "unreachable"]
    """`unsatisfied` — провайдер ответил отказом, устранимым правкой карточки; `unreachable` —
    провайдер не ответил."""


ProviderModelList = ProviderModelsOk | ProviderModelsFailed
"""Перечень моделей провайдера с отметкой момента получения либо причиной отказа."""


class ModelProvider(Protocol):
    @property
    def name(self) -> str:
        """Имя карточки, по которому на провайдера ссылаются записи справочника моделей."""
        ...

    def model_uri(self, identifier: str) -> str:
        """Идентификатор модели в том виде, в каком его принимает API провайдера."""
        ...

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
        """Обращение к модели. Отказ объявляется исключением `ModelFailure`.

        `result_schema` — JSON Schema ответа: модель обязана вернуть объект по ней.
        `reasoning_effort` — объём рассуждения; пусто — по умолчанию модели.
        """
        ...

    async def list_models(self, *, force: bool = False) -> ProviderModelList: ...

    async def aclose(self) -> None: ...
