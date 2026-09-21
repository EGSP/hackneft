"""Обращения к платформе.

Агрегатор — единственная сторона, знающая формат платформы: `source.Reading` переводится в
тело запроса здесь, а не в исполнителе симуляции.
"""

import logging
from dataclasses import dataclass

import httpx

from .source import Reading

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PushResult:
    """Итог отправки одного пакета."""

    accepted: int
    """Число записей, сохранённых платформой."""
    duplicates: int
    """Число записей, отклонённых как повторная доставка уже сохранённого показания."""


class PlatformUnavailable(Exception):
    """Платформа не ответила либо ответила отказом. Курсор при этом не продвигается."""


class PlatformClient:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def base_url(self) -> str:
        return self._base_url

    async def check(self) -> bool:
        """Проверяет доступность платформы. Отказ не считается ошибкой сервиса: состояние
        подключения отображается в веб-интерфейсе, а такты продолжают выполняться."""
        try:
            response = await self._client.get(f"{self._base_url}/health")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def push(self, readings: list[Reading]) -> PushResult:
        """Отправляет пакет показаний запросом `POST /api/sensor-data/bulk`."""
        payload = {
            "items": [
                {
                    "timestamp": reading.timestamp.isoformat(),
                    "sensor_code": reading.sensor_code,
                    "value": reading.value,
                    "source": reading.source,
                }
                for reading in readings
            ]
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/api/sensor-data/bulk", json=payload
            )
        except httpx.HTTPError as failure:
            raise PlatformUnavailable(f"{type(failure).__name__}: {failure}") from failure

        if response.status_code >= 400:
            raise PlatformUnavailable(
                f"платформа ответила кодом {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        return PushResult(
            accepted=int(body.get("accepted", 0)),
            duplicates=int(body.get("duplicates", 0)),
        )
