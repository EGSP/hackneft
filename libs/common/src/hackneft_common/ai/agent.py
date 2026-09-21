"""Справочник агентов.

Карточка агента задаёт системный промпт, закреплённые инструкции и модель сессии. Сессия,
созданная по карточке, получает системный промпт вместе с текстами инструкций и модель,
разрешённую из ссылки карточки. Всё это копируется в сессию при создании, поэтому правка или
удаление карточки и инструкций на созданные сессии не влияет.
"""

from pydantic import Field

from .base import ApiModel
from .model_profile import DEFAULT_MODEL_ALIAS

AGENT_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
"""Идентификатор агента: строчные латинские буквы и цифры, группы разделены дефисом."""


class Agent(ApiModel):
    id: str
    """Идентификатор агента. Задаётся при создании и не меняется: по нему на карточку
    ссылаются сессии."""
    name: str
    description: str
    system_prompt: str
    """Системный промпт агента. Сервис дополняет его указаниями о завершении хода: без них
    модель не знает, как объявить итог."""
    instructions: list[str]
    """Идентификаторы закреплённых инструкций в порядке, в котором их тексты добавляются к
    системному промпту."""
    model: str
    """Ссылка на модель: идентификатор записи справочника, идентификатор модели у провайдера
    либо синоним. Разрешается в запись при создании сессии."""
    created_at: str
    updated_at: str


class CreateAgentRequest(ApiModel):
    id: str = Field(min_length=1, max_length=40, pattern=AGENT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(min_length=1, max_length=100_000)
    instructions: list[str] = Field(default_factory=list, max_length=100)
    model: str = Field(default=DEFAULT_MODEL_ALIAS, min_length=1, max_length=300)


class UpdateAgentRequest(ApiModel):
    """Правка карточки. Идентификатора в ней нет: он не меняется."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=100_000)
    instructions: list[str] | None = Field(default=None, max_length=100)
    """Перечень заменяет прежний целиком."""
    model: str | None = Field(default=None, min_length=1, max_length=300)


class AgentListResponse(ApiModel):
    agents: list[Agent]
