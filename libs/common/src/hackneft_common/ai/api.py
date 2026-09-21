"""Запросы и ответы API сессий."""

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from .base import ApiModel
from .events import SessionEvent
from .session import Session, SessionKind

TEXT_MAX_LENGTH = 100_000
"""Предел длины сообщения и постановки задачи в символах."""


class CreateSessionRequest(ApiModel):
    """Создание сессии.

    Один запрос обслуживает оба вида. Агентская сессия получает постановку задачи сразу и
    начинает ход немедленно; порождающая сессия для неё необязательна — корневую агентскую
    сессию создаёт платформа.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: SessionKind | None = None
    parent_id: str | None = None
    """Порождающая сессия. В её журнал записывается событие о порождении."""
    task: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX_LENGTH)
    """Постановка задачи. Обязательна для вида `agent`, для чата не имеет смысла."""
    tools: list[str] | None = None
    """Имена доступных инструментов. Отсутствие означает весь набор сервиса; перечень сужает
    его, а расширить не может."""
    agent: str | None = None
    """Идентификатор карточки агента. Сессия получает её системный промпт и модель; модель,
    указанная в запросе явно, важнее модели карточки."""
    model: str | None = Field(default=None, min_length=1, max_length=300)
    """Модель сессии: идентификатор записи справочника, идентификатор модели у провайдера либо
    синоним. Ссылка разрешается в запись при создании, и дальше сессия закреплена за ней."""
    model_id: str | None = None
    """Идентификатор записи справочника. Сохранён ради совместимости: поле `model` принимает
    его наравне с другими ссылками."""
    traceparent: str | None = Field(default=None, max_length=200)
    """Контекст трассы вызывающей стороны в формате W3C Trace Context."""


class SendMessageRequest(ApiModel):
    text: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)


class SelectModelRequest(ApiModel):
    """Смена модели сессии. Указывается одно из полей: `model` принимает любую ссылку на модель,
    `model_id` — только идентификатор записи и сохранён ради совместимости."""

    model: str | None = Field(default=None, min_length=1, max_length=300)
    model_id: str | None = None

    @model_validator(mode="after")
    def _one_reference(self) -> Self:
        if (self.model is None) == (self.model_id is None):
            raise ValueError("Укажите ровно одно из полей model и modelId")
        return self

    @property
    def reference(self) -> str:
        """Ссылка на модель из того поля, которое указано."""
        return self.model or self.model_id or ""


class SessionListResponse(ApiModel):
    sessions: list[Session]


class SessionEventsResponse(ApiModel):
    events: list[SessionEvent]
    last_seq: int
    """Порядковый номер последнего события; клиент передаёт его при следующем чтении."""


class ErrorResponse(ApiModel):
    """Тело отказа службы, как в API xip.

    Отдельные отказы несут дополнительные поля рядом с основными — например, перечень моделей,
    ссылающихся на удаляемую карточку провайдера.
    """

    model_config = ConfigDict(extra="allow")

    status_code: int
    error: str
    """Краткое название кода состояния HTTP: `Bad Request`, `Not Found`, `Conflict`."""
    message: str
    """Причина отказа, пригодная для показа человеку."""


class AcceptedResponse(ApiModel):
    accepted: bool
