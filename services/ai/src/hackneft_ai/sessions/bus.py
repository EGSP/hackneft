"""Рассылка событий сессии подписчикам.

Шина живёт в памяти процесса и намеренно не является хранилищем: источник истины — журнал в
базе. Подписчик, переподключившийся после обрыва, догружает пропущенное по порядковому номеру,
а шина отдаёт только то, что происходит сейчас.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from hackneft_common.ai import SessionEvent

SubscriberQueue = asyncio.Queue[SessionEvent | None]
"""Очередь подписчика. `None` означает, что сессия удалена и событий больше не будет."""


class SessionEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[SubscriberQueue]] = {}

    def publish(self, session_id: str, event: SessionEvent) -> None:
        for queue in self._subscribers.get(session_id, ()):
            queue.put_nowait(event)

    @contextmanager
    def subscribe(self, session_id: str) -> Iterator[SubscriberQueue]:
        queue: SubscriberQueue = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(queue)
        try:
            yield queue
        finally:
            queues = self._subscribers.get(session_id)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    del self._subscribers[session_id]

    def close(self, session_id: str) -> None:
        for queue in self._subscribers.pop(session_id, ()):
            queue.put_nowait(None)
