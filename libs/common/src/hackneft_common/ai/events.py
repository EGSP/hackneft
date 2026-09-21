"""События журнала сессии.

Журнал — перечень того, что фактически произошло, записями с порядковым номером. Это не то
же самое, что массив сообщений, отправляемый модели: массив подчиняется формату API и со
временем перестаёт соответствовать происходившему — история сжимается, старые результаты
сворачиваются. Журнал остаётся полным, и массив сообщений строится из него, а не наоборот.
"""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter

from .base import ApiModel
from .session import SessionKind

TurnFailureReason = Literal[
    "model_missing",
    "model_unavailable",
    "model_error",
    "step_limit",
    "output_limit",
    "aborted",
    "internal",
]
"""Исход неудачного хода. Различаются потому, что требуют разной реакции.

`model_missing` — модели сессии нет в справочнике: запись удалена, и нужно выбрать другую
модель; `model_unavailable` — запись есть, но провайдер модель не предоставляет;
`model_error` — отказ на стороне провайдера модели; `step_limit` — сработало ограничение
числа шагов, задача слишком велика для одного хода; `output_limit` — модель израсходовала
выходной бюджет, не сформировав ответ; `aborted` — прерывание по команде; `internal` — дефект
сервиса либо его остановка посреди хода.
"""

ToolOutcome = Literal[
    "ok", "unknown_tool", "bad_arguments", "schema_mismatch", "tool_failure", "defect"
]
"""Исход вызова инструмента.

Несуществующее имя (`unknown_tool`), неразобранный JSON (`bad_arguments`) и несоответствие
схеме (`schema_mismatch`) означают неверный вызов, который модель способна исправить; отказ
инструмента (`tool_failure`) описан им самим; дефект (`defect`) есть ошибка сервиса, исправить
которую вызовом нельзя.
"""


class EventBase(ApiModel):
    """Общие поля события: порядковый номер в журнале и время записи.

    Их проставляет журнал при записи. У события, ещё не записанного, номер равен нулю, а
    время пусто.
    """

    seq: int = Field(default=0, ge=0)
    at: str = ""


class UserMessageEvent(EventBase):
    """Вход хода: сообщение в чат-сессии либо постановка задачи агентской сессии."""

    type: Literal["user_message"] = "user_message"
    text: str


class StepStartedEvent(EventBase):
    """Начало шага.

    Записывается до обращения к модели, поэтому по журналу видно, что ход идёт, ещё до того,
    как модель ответит: обращение к модели занимает почти всё время хода.
    """

    type: Literal["step_started"] = "step_started"
    step: int = Field(ge=1)
    max_steps: int = Field(ge=1)
    provider: str
    """Провайдер модели, к которой обращается шаг."""
    model: str
    """Модель, к которой обращается шаг.

    Записывается в каждый шаг, а не только в сессию: модель сессии можно сменить между
    ходами, и без отметки на шаге нельзя было бы понять, какой моделью получен ответ.
    """
    snapshot_id: str | None = None
    """Снимок постоянной части запроса: системного промпта и описаний инструментов."""


class ModelReplyEvent(EventBase):
    """Ответ модели на обращение шага.

    Записывается после каждого обращения, чем бы оно ни закончилось, поэтому расход токенов
    сохраняется и у хода, завершившегося неудачей. Текст ответа и вызовы сюда не входят — их
    несут `assistant_note`, `tool_call` и `assistant_message`.
    """

    type: Literal["model_reply"] = "model_reply"
    step: int = Field(ge=1)
    prompt_tokens: int = Field(ge=0)
    """Входные токены по данным провайдера: размер запроса целиком."""
    completion_tokens: int = Field(ge=0)
    """Выходные токены по данным провайдера, включая рассуждение."""
    finish_reason: str | None = None
    """Причина остановки генерации по данным провайдера: `stop`, `tool_calls`, `length`."""
    reasoning: str | None = None
    """Рассуждение модели. В диалог не возвращается: модель не ждёт его обратно."""


class AssistantNoteEvent(EventBase):
    """Текст, пришедший от модели вместе с вызовами инструментов."""

    type: Literal["assistant_note"] = "assistant_note"
    step: int = Field(ge=1)
    text: str


class ToolCallEvent(EventBase):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    raw_arguments: str
    step: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    batch_index: int = Field(ge=1)


class ToolResultEvent(EventBase):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    kind: ToolOutcome
    content: str
    duration_ms: int = Field(ge=0)
    step: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    batch_index: int = Field(ge=1)


class AssistantMessageEvent(EventBase):
    """Итоговый ответ хода."""

    type: Literal["assistant_message"] = "assistant_message"
    text: str


class TurnFinishedEvent(EventBase):
    type: Literal["turn_finished"] = "turn_finished"
    steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class TurnFailedEvent(EventBase):
    type: Literal["turn_failed"] = "turn_failed"
    reason: TurnFailureReason
    message: str
    duration_ms: int | None = Field(default=None, ge=0)
    """Длительность хода до отказа. Отсутствует у хода, закрытого сверкой при запуске."""


class ChildSessionStartedEvent(EventBase):
    """Порождена дочерняя сессия.

    Событие записывается в журнал родителя и несёт только идентификатор: содержимое дочерней
    сессии читается её собственным журналом теми же запросами, что и любая другая.
    """

    type: Literal["child_session_started"] = "child_session_started"
    child_id: str
    kind: SessionKind
    title: str


class SessionCompletedEvent(EventBase):
    """Завершение агентской сессии. Чат-сессия его не достигает."""

    type: Literal["session_completed"] = "session_completed"
    result: JsonValue = None


class SessionFailedEvent(EventBase):
    type: Literal["session_failed"] = "session_failed"
    message: str


SessionEvent = Annotated[
    UserMessageEvent
    | StepStartedEvent
    | ModelReplyEvent
    | AssistantNoteEvent
    | ToolCallEvent
    | ToolResultEvent
    | AssistantMessageEvent
    | TurnFinishedEvent
    | TurnFailedEvent
    | ChildSessionStartedEvent
    | SessionCompletedEvent
    | SessionFailedEvent,
    Field(discriminator="type"),
]

SESSION_EVENT_ADAPTER: TypeAdapter[SessionEvent] = TypeAdapter(SessionEvent)
"""Разбор события из словаря либо JSON с выбором класса по полю `type`."""
