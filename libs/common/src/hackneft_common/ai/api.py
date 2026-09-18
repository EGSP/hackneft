"""Запросы и ответы API сессий."""

from pydantic import Field

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
    model_id: str | None = None
    traceparent: str | None = Field(default=None, max_length=200)
    """Контекст трассы вызывающей стороны в формате W3C Trace Context."""


class SendMessageRequest(ApiModel):
    text: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)


class SelectModelRequest(ApiModel):
    model_id: str


class SessionListResponse(ApiModel):
    sessions: list[Session]


class SessionEventsResponse(ApiModel):
    events: list[SessionEvent]
    last_seq: int
    """Порядковый номер последнего события; клиент передаёт его при следующем чтении."""


class AcceptedResponse(ApiModel):
    accepted: bool
