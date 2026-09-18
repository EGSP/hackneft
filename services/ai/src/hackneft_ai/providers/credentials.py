"""Учётные данные провайдера.

Провайдер получает значение заголовка `Authorization` на каждый запрос, а не один раз при
создании: у Yandex токен короткоживущий и перевыпускается по мере истечения.
"""

from typing import Protocol


class Credentials(Protocol):
    async def authorization(self) -> str | None:
        """Значение заголовка `Authorization` либо `None`, если аутентификация не нужна."""
        ...


class NoCredentials:
    """Аутентификация не требуется: локальный сервер моделей без ключа."""

    async def authorization(self) -> str | None:
        return None


class BearerToken:
    """Постоянный токен: ключ локального сервера либо готовый IAM-токен Yandex."""

    def __init__(self, token: str) -> None:
        self._value = f"Bearer {token}"

    async def authorization(self) -> str | None:
        return self._value
