import logging

import httpx

from hackneft_platform.config import ai_service_url
from hackneft_platform.events import SensorDataCreated, dispatcher

logger = logging.getLogger(__name__)

# Таймаут запроса к ИИ-сервису. Событие уже публикуется в фоне (см. api.py), поэтому здесь
# не нужен короткий таймаут — важно лишь не держать соединение бесконечно.
_REQUEST_TIMEOUT_S = 10.0


# Подписчик диспетчера: уведомляет ИИ-сервис о новой записи показания датчика, создавая
# для неё агентскую сессию. Необязательная надстройка — ядро платформы про ИИ-сервис
# ничего не знает, этот модуль просто зарегистрирован в диспетчере как один из подписчиков.
#
# Событие несёт sensor_code, а не человекочитаемое имя (имя не хранится в измерении,
# см. models.py) — в задачу для агента код подставляется как есть.
@dispatcher.on()
async def notify_ai(event: SensorDataCreated) -> None:
    payload = {
        "kind": "agent",
        "title": f"Новое показание {event.sensor_code}",
        "task": (
            f"Поступило новое показание датчика {event.sensor_code}: "
            f"значение {event.value} в момент {event.timestamp.isoformat()}, "
            f"источник {event.source}."
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            response = await client.post(f"{ai_service_url()}/api/sessions", json=payload)
            response.raise_for_status()
    except httpx.HTTPError:
        # Диспетчер и так изолирует ошибки подписчиков друг от друга (см. events.py), но
        # ошибка логируется и здесь — с указанием события, а не только имени функции.
        logger.exception(
            "Не удалось создать сессию ИИ-сервиса для события %s", event.event_id
        )
