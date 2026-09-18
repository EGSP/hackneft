"""Журнал сессии: запись событий и рассылка их подписчикам.

Порядковый номер берётся из счётчика событий сессии, увеличиваемого в той же транзакции, что
и вставка записи. Это даёт монотонную нумерацию без отдельной последовательности.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hackneft_common.ai import (
    SESSION_EVENT_ADAPTER,
    SessionCompletedEvent,
    SessionEvent,
    SessionFailedEvent,
)

from ..db.database import Database
from ..db.schema import SessionEventRow, SessionRow, iso, utc_now
from ..errors import NotFoundError
from .bus import SessionEventBus


class SessionJournal:
    def __init__(self, db: Database, bus: SessionEventBus) -> None:
        self._db = db
        self._bus = bus

    def for_session(self, session_id: str) -> "BoundJournal":
        """Журнал для ядра, замкнутый на одну сессию."""
        return BoundJournal(self, session_id)

    async def append(self, session_id: str, event: SessionEvent) -> SessionEvent:
        async with self._db.write() as tx:
            row = await _insert(tx, session_id, event)
        return self._publish(session_id, row)

    async def settle(
        self, session_id: str, outcome: SessionCompletedEvent | SessionFailedEvent
    ) -> bool:
        """Исход агентской сессии: событие журнала и итоговое состояние записи.

        Записываются одной транзакцией, иначе могли бы разойтись: остановка сервиса между
        двумя записями оставила бы исход в журнале, а в записи — состояние `running`.

        Исход записывается один раз, и признаком служит само состояние записи: пока оно не
        терминальное, исхода нет. Повторный вызов ничего не делает. Возвращает, записан ли
        исход этим вызовом.
        """
        values: dict[str, object] = (
            {"status": "completed", "result": outcome.result, "failure_message": None}
            if isinstance(outcome, SessionCompletedEvent)
            else {"status": "failed", "failure_message": outcome.message}
        )
        async with self._db.write() as tx:
            changed = await tx.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id, SessionRow.status.in_(("idle", "running")))
                .values(**values)
                .returning(SessionRow.id)
                .execution_options(synchronize_session=False)
            )
            if changed.scalar_one_or_none() is None:
                return False
            row = await _insert(tx, session_id, outcome)
        self._publish(session_id, row)
        return True

    async def read(self, session_id: str, after_seq: int = 0) -> list[SessionEvent]:
        async with self._db.read() as session:
            rows = await session.scalars(
                select(SessionEventRow)
                .where(SessionEventRow.session_id == session_id, SessionEventRow.seq > after_seq)
                .order_by(SessionEventRow.seq)
            )
            return [to_event(row) for row in rows]

    def _publish(self, session_id: str, row: SessionEventRow) -> SessionEvent:
        event = to_event(row)
        self._bus.publish(session_id, event)
        return event


class BoundJournal:
    """Журнал одной сессии — реализация зависимости ядра `Journal`."""

    def __init__(self, journal: SessionJournal, session_id: str) -> None:
        self._journal = journal
        self._session_id = session_id

    async def append(self, event: SessionEvent) -> SessionEvent:
        return await self._journal.append(self._session_id, event)


async def _insert(tx: AsyncSession, session_id: str, event: SessionEvent) -> SessionEventRow:
    """Вставка события с очередным порядковым номером. Выполняется внутри транзакции."""
    now = utc_now()
    # Отметка о последнем событии ставится здесь же: список сессий упорядочен по ней.
    counted = await tx.execute(
        update(SessionRow)
        .where(SessionRow.id == session_id)
        .values(event_count=SessionRow.event_count + 1, last_event_at=now)
        .returning(SessionRow.event_count)
        .execution_options(synchronize_session=False)
    )
    seq = counted.scalar_one_or_none()
    if seq is None:
        raise NotFoundError(f"Сессия {session_id} не найдена")
    row = SessionEventRow(
        session_id=session_id,
        seq=seq,
        type=event.type,
        payload=event.model_dump(mode="json", exclude={"seq", "at", "type"}, exclude_none=True),
        created_at=now,
    )
    tx.add(row)
    await tx.flush()
    return row


def to_event(row: SessionEventRow) -> SessionEvent:
    return SESSION_EVENT_ADAPTER.validate_python(
        {**row.payload, "type": row.type, "seq": row.seq, "at": iso(row.created_at)}
    )
