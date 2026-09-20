"""Подписчики двух рядов серы: передача новых показаний в открытые SSE-соединения.

Обработчики зарегистрированы на конкретные коды датчиков (Q21 — поточный анализатор
ПАК, L21 — лабораторный анализ ЛИМС), а не на все события: главная страница интерфейса
показывает только эти два ряда, и рассылать ей показания расхода или температуры
незачем.

Адресатов у подписчика несколько и число их меняется — каждое открытое SSE-соединение
держит собственную очередь. Соединения регистрируются в `subscribers` эндпоинтом
/api/stream (см. api.py) и удаляются оттуда же при разрыве.
"""

import asyncio
import logging

from hackneft_platform.catalog import SULFUR_LIMS_CODE, SULFUR_PAK_CODE
from hackneft_platform.events import SensorDataCreated, dispatcher

logger = logging.getLogger(__name__)

# Очереди открытых SSE-соединений. Множество живёт в памяти процесса, поэтому
# платформа обязана работать в один рабочий процесс uvicorn: при нескольких процессах
# событие, опубликованное в одном из них, до соединений остальных не дойдёт.
subscribers: set[asyncio.Queue[SensorDataCreated]] = set()


def _broadcast(event: SensorDataCreated) -> None:
    """Кладёт событие в очередь каждого открытого соединения.

    put_nowait, а не await put: обработчик вызывается из фоновой задачи запроса
    на запись показания, и медленный или зависший клиент не должен эту задачу
    задерживать. Переполненная очередь означает, что клиент не успевает читать
    поток; событие для него теряется, а следующий обычный запрос к /api/sensor-data/view
    восстановит пропуск.
    """
    for queue in subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Очередь SSE-подписчика переполнена, событие %s пропущено",
                event.event_id,
            )


@dispatcher.on(SULFUR_PAK_CODE)
async def stream_pak_sulfur(event: SensorDataCreated) -> None:
    """Новое показание поточного анализатора серы (ПАК)."""
    _broadcast(event)


@dispatcher.on(SULFUR_LIMS_CODE)
async def stream_lims_sulfur(event: SensorDataCreated) -> None:
    """Новый результат лабораторного анализа серы (ЛИМС)."""
    _broadcast(event)
