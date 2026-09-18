import logging
import os
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Обработчик событий: уведомляет внешний AI-сервис о новых показаниях датчиков


class AiAgentEventHandler:
    def __init__(self) -> None:
        # URL AI-сервиса, куда отправляются события (можно переопределить через переменную окружения)
        self.ai_service_url = os.getenv(
            "AI_SERVICE_URL",
            "http://localhost:8001",
        ).rstrip("/")

    # Вызывается фоновой задачей из api.py после успешного создания записи в sensor_data.
    # Отправляет событие "sensor_data.created" в AI-сервис; ошибки доставки только логируются,
    # чтобы не влиять на ответ клиенту, инициировавшему запись.
    async def handle_sensor_data_created(
        self,
        *,
        event_id: str,
        data_id: int,
        timestamp: datetime,
        sensor_name: str,
        sensor_tag: str,
        value: float,
        source: str,
    ) -> None:
        event: dict[str, Any] = {
            "event_id": event_id,
            "event_type": "sensor_data.created",
            "aggregate_id": data_id,
            "payload": {
                "id": data_id,
                "timestamp": timestamp.isoformat(),
                "sensor_name": sensor_name,
                "sensor_tag": sensor_tag,
                "value": value,
                "source": source,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.ai_service_url}/agents/run",
                    json=event,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            logger.exception(
                "Failed to deliver event %s to AI service",
                event_id,
            )


# Синглтон-инстанс обработчика, используется в api.py
ai_agent_event_handler = AiAgentEventHandler()
