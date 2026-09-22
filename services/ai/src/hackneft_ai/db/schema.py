"""Схема базы данных сервиса."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Dialect, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from hackneft_common.ai import DEFAULT_MODEL_ALIAS


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


def iso(value: datetime) -> str:
    """Время в формате ISO 8601 с точностью до миллисекунд и отметкой UTC."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class UtcDateTime(TypeDecorator[datetime]):
    """Время в UTC. SQLite часового пояса не хранит, поэтому он снимается при записи и
    восстанавливается при чтении."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class ProviderRow(Base):
    """Справочник провайдеров моделей."""

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    """Имя карточки. Задаётся при создании и не меняется: по нему на карточку ссылаются записи
    справочника моделей."""
    title: Mapped[str | None]
    type: Mapped[str] = mapped_column(String(32))
    secrets: Mapped[dict[str, str]] = mapped_column(JSON)
    """Секреты в том виде, в каком переданы: ключи env-файла и их значения. Хранятся открытым
    текстом, как в env-файле; наружу значения собственно секретов не выдаются."""
    check_status: Mapped[str] = mapped_column(String(16), default="unknown")
    last_check_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_check_message: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class LlmModelRow(Base):
    """Справочник моделей. Единственный источник модели хода."""

    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(40))
    identifier: Mapped[str] = mapped_column(String(300))
    """Задаётся при создании и не меняется: запись и модель соответствуют друг другу
    однозначно, и сессия, закреплённая за записью, не может перейти на другую модель без
    явного выбора."""
    alias: Mapped[str] = mapped_column(
        String(40), default=DEFAULT_MODEL_ALIAS, server_default=DEFAULT_MODEL_ALIAS
    )
    """Синоним модели. Может совпадать у нескольких записей, поэтому не уникален."""
    problems: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    """Неполадки записи, найденные проверкой справочника. Пересчитываются при запуске сервиса
    и при каждом изменении справочника."""
    is_default: Mapped[bool] = mapped_column(default=False)
    supports_tools: Mapped[bool] = mapped_column(default=False)
    supports_reasoning: Mapped[bool] = mapped_column(default=False)
    availability: Mapped[str] = mapped_column(String(16), default="unknown")
    """Обновляется по таймеру, поэтому хранится, а не вычисляется при каждом обращении."""
    last_check_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_check_message: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (UniqueConstraint("provider", "identifier"),)


class InstructionRow(Base):
    """Справочник инструкций: как действовать в определённой ситуации."""

    __tablename__ = "instructions"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    """Задаётся при создании и не меняется: по нему на инструкцию ссылаются карточки агентов."""
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(default="", server_default="")
    text: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class AgentRow(Base):
    """Справочник агентов: системный промпт и модель сессии."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    """Задаётся при создании и не меняется: по нему на карточку ссылаются сессии."""
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(default="")
    system_prompt: Mapped[str]
    instructions: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    """Идентификаторы закреплённых инструкций. Хранятся перечнем, а не связующей таблицей:
    порядок в нём значим, а размер мал."""
    model: Mapped[str] = mapped_column(String(300))
    """Ссылка на модель. Разрешается в запись справочника при создании сессии."""
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str]
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="idle")
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    """Порождающая сессия. Удаление родителя уносит потомков — по отдельности они лишены
    смысла."""
    result: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    failure_message: Mapped[str | None]
    event_count: Mapped[int] = mapped_column(default=0)
    """Число событий в журнале и источник порядкового номера: запись события и увеличение
    счётчика идут одной транзакцией, что даёт монотонную нумерацию."""
    last_event_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    """Время последнего события. Порядок в списке должен отражать работу в сессии, а не любое
    изменение её записи."""
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    """Заметки агента — состояние инструментов `read_notes` и `write_note`."""
    model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_models.id", ondelete="SET NULL"))
    model_provider: Mapped[str | None] = mapped_column(String(40))
    model_identifier: Mapped[str | None] = mapped_column(String(300))
    """Модель, назначенная сессии. Хранится отдельно от связи: при удалении записи
    справочника связь обнуляется, а отметка остаётся, и по ней ход отличает сессию, чья модель
    удалена, от сессии без модели."""
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    system_prompt: Mapped[str | None]
    """Системный промпт карточки агента, скопированный при создании сессии: правка карточки
    не меняет поведение уже идущих диалогов. Пусто — промпт сервиса по умолчанию."""
    input: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    """Исходные данные агентской сессии; их дословно передаёт дочернему агенту `run_agent`."""
    result_schema: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    """JSON Schema итога. Пусто — итогом служит текст терминального вызова."""
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (Index("ix_sessions_last_event_at", "last_event_at"),)


class SessionEventRow(Base):
    """Событие сессии. Записи добавляются в конец и не изменяются."""

    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    seq: Mapped[int]
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)

    __table_args__ = (UniqueConstraint("session_id", "seq"),)


class RequestSnapshotRow(Base):
    """Снимок постоянной части запроса к модели.

    Идентификатор — хеш содержимого, и одинаковое содержимое хранится одной записью. С
    сессиями снимок не связан: на одну запись ссылаются все сессии с тем же набором.
    """

    __tablename__ = "request_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt: Mapped[str]
    sections: Mapped[list[str]] = mapped_column(JSON)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class McpConnectionRow(Base):
    """Подключение MCP: внешний сервер, поставляющий сервису инструменты."""

    __tablename__ = "mcp_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(24), unique=True)
    """Имя подключения. Оно же префикс имён инструментов, поэтому уникально."""
    title: Mapped[str | None]
    enabled: Mapped[bool] = mapped_column(default=True)
    transport: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    """Транспорт и его настройки. Секреты — заголовки и переменные окружения — хранятся здесь
    и только здесь."""
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    """Снимок состава, полученный при последнем обнаружении."""
    tool_mode: Mapped[str] = mapped_column(String(16), default="all")
    enabled_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    check_status: Mapped[str] = mapped_column(String(16), default="unknown")
    problems: Mapped[list[str]] = mapped_column(JSON, default=list)
    stale: Mapped[bool] = mapped_column(default=False)
    last_check_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_check_message: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class CatalogMigrationRow(Base):
    """Однократные изменения каталога и резервная копия прежних карточек."""

    __tablename__ = "catalog_migrations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
