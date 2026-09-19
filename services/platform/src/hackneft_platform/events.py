import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Событие: создана новая запись показания датчика.
# Несёт код датчика (sensor_code), а не его имя — имя не хранится в измерении
# (см. models.py), подписчику, которому оно нужно, придётся сходить в sensor_query сам.


@dataclass(frozen=True)
class SensorDataCreated:
    event_id: str
    data_id: int
    timestamp: datetime
    sensor_code: str
    value: float
    source: str


Handler = Callable[[SensorDataCreated], Awaitable[None]]


# Диспетчер событий: обработчики регистрируются через `on()`, эндпоинт
# только публикует событие через `publish()` и не знает, кто и сколько
# на него подписано.
class EventDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str | None, list[Handler]] = defaultdict(list)

    def on(self, sensor_code: str | None = None) -> Callable[[Handler], Handler]:
        """Регистрирует обработчик события SensorDataCreated.

        sensor_code=None — обработчик получает события всех датчиков,
        иначе — только события с указанным sensor_code.
        """

        def register(fn: Handler) -> Handler:
            self._handlers[sensor_code].append(fn)
            return fn

        return register

    async def publish(self, event: SensorDataCreated) -> None:
        handlers = self._handlers[None] + self._handlers[event.sensor_code]
        for fn in handlers:
            try:
                await fn(event)
            except Exception:
                logger.exception("Обработчик %s завершился ошибкой", fn.__name__)


# Синглтон-инстанс диспетчера, используется в api.py и модулях обработчиков.
dispatcher = EventDispatcher()
