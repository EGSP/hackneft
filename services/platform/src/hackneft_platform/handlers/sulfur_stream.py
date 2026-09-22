"""Ряды главного экрана: сера, температура реактора и подача сырья в SSE.

Ряды определяются именами из справочника sensor_names («ПАК» и «ЛИМС»), а не кодами
датчиков: главная страница запрашивает их по именам, и поток событий обязан отбирать
показания по тому же признаку. Поэтому обработчик подписан на события всех датчиков и
сам сверяет код пришедшего показания с кодами, на которые эти имена указывают сейчас.
Если имя переносится на другой код (сменился источник данных, код получил приставку
установки), правится строка справочника — ни этот модуль, ни интерфейс не меняются.

Адресатов у подписчика несколько и число их меняется — каждое открытое SSE-соединение
держит собственную очередь. Соединения регистрируются в `subscribers` эндпоинтом
/api/stream (см. api.py) и удаляются оттуда же при разрыве.
"""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select

from hackneft_platform.catalog import SULFUR_NAMES
from hackneft_platform.db import SessionLocal
from hackneft_platform.events import SensorDataCreated, dispatcher
from hackneft_platform.models import SensorName

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SulfurReading:
    """Показание одного из рядов главного экрана вместе с именем ряда.

    Имя добавлено к событию здесь: само событие несёт только код датчика (см. events.py),
    а получателю потока нужен тот признак, по которому он запрашивал ряды, — имя.
    """

    event: SensorDataCreated
    name: str


# Очереди открытых SSE-соединений. Множество живёт в памяти процесса, поэтому
# платформа обязана работать в один рабочий процесс uvicorn: при нескольких процессах
# событие, опубликованное в одном из них, до соединений остальных не дойдёт.
subscribers: set[asyncio.Queue[SulfurReading]] = set()

# Соответствие «имя ряда — код датчика», прочитанное из справочника. Справочник меняется
# редко, а событие приходит на каждое показание, поэтому соответствие удерживается в
# памяти; правка справочника сбрасывает его вызовом reset_tracked_codes (см. api.py).
_tracked_codes: dict[str, str] | None = None


def tracked_codes() -> dict[str, str]:
    """Коды датчиков, на которые указывают имена рядов серы, по одному на имя."""
    global _tracked_codes
    if _tracked_codes is None:
        with SessionLocal() as session:
            rows = session.execute(
                select(SensorName.name, SensorName.sensor_code).where(
                    SensorName.name.in_(SULFUR_NAMES)
                )
            ).all()
        _tracked_codes = {row.name: row.sensor_code for row in rows}

        missing = [name for name in SULFUR_NAMES if name not in _tracked_codes]
        if missing:
            # Отсутствие имени в справочнике не прерывает работу платформы: показания
            # продолжают сохраняться, но соответствующий ряд в поток не попадает, и
            # причину такого поведения нужно видеть в журнале.
            logger.warning(
                "Имена рядов серы отсутствуют в справочнике: %s", ", ".join(missing)
            )
    return _tracked_codes


def reset_tracked_codes() -> None:
    """Сбрасывает запомненное соответствие. Вызывается после правки справочника имён."""
    global _tracked_codes
    _tracked_codes = None


def _broadcast(reading: SulfurReading) -> None:
    """Кладёт показание в очередь каждого открытого соединения.

    put_nowait, а не await put: обработчик вызывается из фоновой задачи запроса
    на запись показания, и медленный или зависший клиент не должен эту задачу
    задерживать. Переполненная очередь означает, что клиент не успевает читать
    поток; событие для него теряется, а следующий обычный запрос к /api/sensor-data/view
    восстановит пропуск.
    """
    for queue in subscribers:
        try:
            queue.put_nowait(reading)
        except asyncio.QueueFull:
            logger.warning(
                "Очередь SSE-подписчика переполнена, событие %s пропущено",
                reading.event.event_id,
            )


@dispatcher.on()
async def stream_sulfur(event: SensorDataCreated) -> None:
    """Новое показание серы, температуры на входе реактора либо подачи сырья.

    Подписка оформлена на события всех датчиков, поскольку код отслеживаемого датчика
    задан не в коде платформы, а справочником имён и потому во время работы известен
    только из него. Показания вне четырёх рядов отсеиваются проверкой ниже и до
    соединений не доходят.
    """
    # Два дополнительных графика используют коды 24-2000; ряды серы по-прежнему
    # выбираются через настраиваемые имена справочника.
    if event.sensor_code in ("ht_t6", "ht_f9"):
        _broadcast(SulfurReading(event=event, name=event.sensor_code))
    for name, code in tracked_codes().items():
        if code == event.sensor_code:
            _broadcast(SulfurReading(event=event, name=name))
