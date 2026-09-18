"""Получение IAM-токена Yandex Cloud по ключу сервисного аккаунта.

Токен короткоживущий (около двенадцати часов), поэтому готовым он не хранится: сервис сам
подписывает JWT ключом сервисного аккаунта и обменивает его на IAM-токен — тот же поток, что
выполняет `yc iam create-token`, — и перевыпускает токен заранее, до истечения.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx2
import jwt

from .retry import describe_error_chain, with_retry

IAM_TOKENS_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
_JWT_ALGORITHM = "PS256"
"""Алгоритм подписи, который требует Yandex: RSASSA-PSS с SHA-256."""
_JWT_TTL_S = 3600
_REFRESH_SKEW_S = 5 * 60
"""Перевыпуск заранее, за этот зазор до истечения."""
_DEFAULT_TTL_S = 50 * 60
"""Срок, принимаемый при отсутствии `expiresAt` в ответе."""

_PKCS8_PEM = re.compile(r"-----BEGIN PRIVATE KEY-----.*?-----END PRIVATE KEY-----", re.DOTALL)
"""В поле `private_key` файла authorized_key.json Yandex добавляет перед PEM строку
«PLEASE DO NOT REMOVE THIS LINE! …», которую разбор ключа не принимает."""


@dataclass(frozen=True, slots=True)
class ServiceAccountKey:
    """Авторизованный ключ сервисного аккаунта Yandex Cloud."""

    key_id: str
    service_account_id: str
    private_key: str


class YandexAuthError(Exception):
    """Отказ аутентификации.

    `unsatisfied` — отказ, устранимый правкой секретов: неверный ключ либо отказ IAM в выдаче
    токена. `unreachable` — сервис IAM не ответил.
    """

    def __init__(
        self, message: str, kind: Literal["unsatisfied", "unreachable"] = "unsatisfied"
    ) -> None:
        super().__init__(message)
        self.kind = kind


def sign_jwt(key: ServiceAccountKey) -> str:
    """Подписывает JWT для обмена на IAM-токен. Проверяет заодно, что ключ пригоден."""
    pem = _PKCS8_PEM.search(key.private_key)
    if pem is None:
        raise YandexAuthError(
            "YANDEX_PRIVATE_KEY: не найден блок BEGIN/END PRIVATE KEY. Передайте значение "
            "private_key из authorized_key.json целиком."
        )
    now = int(time.time())
    payload = {
        "iss": key.service_account_id,
        "aud": IAM_TOKENS_URL,
        "iat": now,
        "exp": now + _JWT_TTL_S,
    }
    try:
        return jwt.encode(
            payload, pem.group(0), algorithm=_JWT_ALGORITHM, headers={"kid": key.key_id}
        )
    except (ValueError, TypeError, jwt.PyJWTError) as error:
        raise YandexAuthError(
            f"YANDEX_PRIVATE_KEY: ключ не пригоден для подписи: {error}"
        ) from error


class YandexIamCredentials:
    """Учётные данные провайдера Yandex. Параллельные обращения при истёкшем токене
    дожидаются одного обмена, а не порождают по обмену на каждое."""

    def __init__(self, key: ServiceAccountKey, http: httpx2.AsyncClient) -> None:
        self._key = key
        self._http = http
        self._token: str | None = None
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()

    async def authorization(self) -> str | None:
        return f"Bearer {await self.token()}"

    async def token(self) -> str:
        if self._token is not None and time.time() < self._refresh_at:
            return self._token
        async with self._lock:
            if self._token is None or time.time() >= self._refresh_at:
                self._token, self._refresh_at = await self._request_token()
            return self._token

    async def _request_token(self) -> tuple[str, float]:
        signed = sign_jwt(self._key)
        try:
            response = await with_retry(
                lambda: self._http.post(IAM_TOKENS_URL, json={"jwt": signed}, timeout=20)
            )
        except httpx2.HTTPError as error:
            raise YandexAuthError(
                f"Не удалось обратиться к сервису IAM: {describe_error_chain(error)}",
                "unreachable",
            ) from error

        if response.status_code >= 400:
            raise YandexAuthError(
                f"Обмен JWT на IAM-токен не удался: HTTP {response.status_code}. "
                f"{response.text[:500]}".strip(),
                "unreachable" if response.status_code >= 500 else "unsatisfied",
            )

        data = response.json()
        token = data.get("iamToken") if isinstance(data, dict) else None
        if not isinstance(token, str) or token == "":
            raise YandexAuthError("Ответ IAM не содержит поля iamToken")

        expires_at = _parse_timestamp(data.get("expiresAt"))
        refresh_at = (
            time.time() + _DEFAULT_TTL_S if expires_at is None else expires_at - _REFRESH_SKEW_S
        )
        return token, refresh_at


def _parse_timestamp(value: object) -> float | None:
    """Время RFC 3339 из ответа IAM. Дробная часть бывает длиннее микросекунд — она усекается."""
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None
