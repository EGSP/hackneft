"""Справочник моделей.

Профиль модели — запись в справочнике сервиса. Справочник один на весь сервис, как и пометка
модели по умолчанию, и он единственный источник модели: в конфигурации модель не задаётся, и
пока справочник пуст, ход агента не запускается. Запись ссылается на карточку провайдера по
её имени.

Модель указывается ссылкой: идентификатором записи, идентификатором модели у провайдера либо
синонимом. Синоним короче идентификатора и может быть общим у нескольких записей — тогда он
обозначает класс моделей, например `llm-medium`, и справочник выбирает из них одну.
"""

from typing import Literal

from pydantic import Field

from .base import ApiModel
from .session import SessionKind

DEFAULT_MODEL_ALIAS = "llm-medium"
"""Синоним, который запись получает, если он не указан при создании."""

MODEL_ALIAS_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
"""Синоним: строчные латинские буквы и цифры, группы разделены одиночным дефисом."""

ModelAvailability = Literal["unknown", "available", "not_listed", "unreachable"]
"""Доступность модели.

`available` — модель присутствует в перечне провайдера. `not_listed` — перечень получен, модели
в нём нет: скорее всего ошибка в идентификаторе либо модель отключена. `unreachable` — перечень
получить не удалось, и о самой модели ничего не известно. `unknown` — проверка ещё не
выполнялась. Различие между `not_listed` и `unreachable` существенно: в первом случае неверна
запись, во втором — окружение, и правка записи ничего не даст.
"""


class ModelSession(ApiModel):
    """Сессия, в которой прямо сейчас идёт ход на модели."""

    id: str
    title: str
    kind: SessionKind


class ModelProfile(ApiModel):
    id: str
    provider: str
    """Имя карточки провайдера в справочнике провайдеров."""
    identifier: str
    """Имя модели у провайдера.

    У Yandex — короткое имя (`qwen3.6-35b-a3b/latest`), дополняемое каталогом из карточки
    провайдера, либо полный URI. Задаётся при создании и не меняется, поэтому запись и модель
    соответствуют друг другу однозначно.
    """
    alias: str
    """Синоним модели. Используется наравне с идентификатором и может совпадать у нескольких
    записей. Из записей с общим синонимом выбирается модель по умолчанию, затем доступная,
    затем добавленная раньше."""
    problems: list[str]
    """Неполадки записи, найденные проверкой справочника: например, синоним, совпадающий с
    идентификатором другой модели. Пустой перечень означает, что неполадок нет."""
    is_default: bool
    supports_tools: bool
    """Признак, проставляемый вручную: перечень моделей провайдера сведений о возможностях не
    содержит."""
    supports_reasoning: bool
    availability: ModelAvailability
    last_check_at: str | None
    last_check_message: str | None
    created_at: str
    active_sessions: list[ModelSession]
    """Сессии, в которых прямо сейчас идёт ход на этой модели."""


class CreateModelProfileRequest(ApiModel):
    provider: str = Field(min_length=1, max_length=40)
    identifier: str = Field(min_length=1, max_length=300)
    alias: str | None = Field(default=None, max_length=40, pattern=MODEL_ALIAS_PATTERN)
    """Отсутствие означает синоним по умолчанию `llm-medium`."""
    is_default: bool | None = None
    supports_tools: bool | None = None
    supports_reasoning: bool | None = None


class UpdateModelProfileRequest(ApiModel):
    """Правка записи. Провайдера и идентификатора в ней нет: они не меняются. Синоним
    изменяем: сессия закреплена за записью, а не за синонимом."""

    alias: str | None = Field(default=None, max_length=40, pattern=MODEL_ALIAS_PATTERN)
    is_default: bool | None = None
    supports_tools: bool | None = None
    supports_reasoning: bool | None = None


class ModelListResponse(ApiModel):
    models: list[ModelProfile]


class ModelInUseError(ApiModel):
    """Отказ в удалении модели, которой исполняются ходы: тело ответа с кодом 409."""

    status_code: Literal[409] = 409
    error: str
    message: str
    sessions: list[ModelSession]
