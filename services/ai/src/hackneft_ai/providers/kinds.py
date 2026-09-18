"""Типы провайдеров: допустимые секреты, их разбор и сборка провайдера по карточке.

Секреты карточки — один объект с ключами env-файла. Каждый тип объявляет свои ключи и то,
какие из них скрываются в ответах API: каталог и адрес показываются как есть, а ключ и токен
заменяются маской.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx2

from hackneft_common.ai import SECRET_MASK, ProviderType

from .base import ModelProvider
from .credentials import BearerToken, Credentials, NoCredentials
from .openai_compatible import OpenAICompatibleProvider
from .yandex_iam import ServiceAccountKey, YandexAuthError, YandexIamCredentials, sign_jwt

YANDEX_BASE_URL = "https://llm.api.cloud.yandex.net/v1"


class SecretsError(Exception):
    """Секреты карточки непригодны. Несёт перечень проблем целиком, а не первую из них."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class SecretField:
    name: str
    hidden: bool
    """Значение заменяется маской в ответах API."""


FIELDS: dict[ProviderType, tuple[SecretField, ...]] = {
    # Ключи совпадают с переменными env-файла xip.
    "yandex": (
        SecretField("YANDEX_FOLDER_ID", hidden=False),
        SecretField("YANDEX_BASE_URL", hidden=False),
        SecretField("YANDEX_IAM_TOKEN", hidden=True),
        SecretField("YANDEX_KEY_ID", hidden=False),
        SecretField("YANDEX_SERVICE_ACCOUNT_ID", hidden=False),
        SecretField("YANDEX_PRIVATE_KEY", hidden=True),
    ),
    # Ключи совпадают с переменными, которые читает SDK OpenAI.
    "openai_compatible": (
        SecretField("OPENAI_BASE_URL", hidden=False),
        SecretField("OPENAI_API_KEY", hidden=True),
    ),
}


@dataclass(frozen=True, slots=True)
class YandexSettings:
    folder_id: str
    base_url: str
    auth: ServiceAccountKey | str
    """Ключ сервисного аккаунта либо готовый IAM-токен."""


@dataclass(frozen=True, slots=True)
class OpenAICompatibleSettings:
    base_url: str
    api_key: str | None


def parse(
    provider_type: ProviderType, secrets: Mapping[str, str]
) -> YandexSettings | OpenAICompatibleSettings:
    """Разбирает секреты карточки. Отказ объявляется исключением `SecretsError`."""
    problems = _unknown_keys(provider_type, secrets)
    settings = (
        _parse_yandex(secrets, problems)
        if provider_type == "yandex"
        else _parse_openai(secrets, problems)
    )
    if problems or settings is None:
        raise SecretsError(problems)
    return settings


def build_provider(
    name: str, provider_type: ProviderType, secrets: Mapping[str, str], http: httpx2.AsyncClient
) -> ModelProvider:
    """Собирает провайдер по карточке. Отказ объявляется исключением `SecretsError`."""
    settings = parse(provider_type, secrets)
    if isinstance(settings, OpenAICompatibleSettings):
        credentials: Credentials = (
            NoCredentials() if settings.api_key is None else BearerToken(settings.api_key)
        )
        return OpenAICompatibleProvider(
            name=name,
            base_url=settings.base_url,
            credentials=credentials,
            http=http,
            status_hints={
                401: "Проверьте OPENAI_API_KEY в карточке провайдера.",
                404: "Проверьте OPENAI_BASE_URL и то, что модель с таким идентификатором "
                "загружена на сервере.",
            },
        )

    prefix = f"gpt://{settings.folder_id}/"
    credentials = (
        BearerToken(settings.auth)
        if isinstance(settings.auth, str)
        else YandexIamCredentials(settings.auth, http)
    )
    return OpenAICompatibleProvider(
        name=name,
        base_url=settings.base_url,
        credentials=credentials,
        http=http,
        # Короткое имя модели дополняется каталогом, поэтому смена каталога не требует правки
        # записей справочника моделей. Полный URI передаётся как есть.
        resolve_model=lambda model: model if model.startswith("gpt://") else prefix + model,
        short_name=lambda full: full.removeprefix(prefix),
        list_headers={"x-folder-id": settings.folder_id},
        project=settings.folder_id,
        status_hints={
            401: "Проверьте YANDEX_KEY_ID, YANDEX_SERVICE_ACCOUNT_ID и YANDEX_PRIVATE_KEY в "
            "карточке провайдера.",
            403: "Проверьте роль ai.languageModels.user у сервисного аккаунта и YANDEX_FOLDER_ID.",
            404: "Проверьте идентификатор модели в справочнике и YANDEX_FOLDER_ID: URI модели "
            "должен существовать в каталоге.",
        },
    )


def masked(provider_type: ProviderType, secrets: Mapping[str, str]) -> dict[str, str]:
    """Секреты для ответа API: значения скрываемых ключей заменены маской."""
    shown = {field.name for field in FIELDS[provider_type] if not field.hidden}
    return {key: value if key in shown else SECRET_MASK for key, value in secrets.items()}


def merge(current: Mapping[str, str], incoming: Mapping[str, str]) -> dict[str, str]:
    """Секреты после правки.

    Переданный объект заменяет прежний целиком; значение, равное маске, означает «оставить
    прежнее значение этого ключа».
    """
    merged: dict[str, str] = {}
    problems: list[str] = []
    for key, value in incoming.items():
        if value != SECRET_MASK:
            merged[key] = value
        elif key in current:
            merged[key] = current[key]
        else:
            problems.append(f"{key}: передана маска, но прежнего значения нет. Передайте значение.")
    if problems:
        raise SecretsError(problems)
    return merged


def _unknown_keys(provider_type: ProviderType, secrets: Mapping[str, str]) -> list[str]:
    allowed = [field.name for field in FIELDS[provider_type]]
    unknown = [key for key in secrets if key not in allowed]
    problems = []
    if "YANDEX_MODEL" in unknown:
        unknown.remove("YANDEX_MODEL")
        problems.append(
            "YANDEX_MODEL не принимается: модель задаётся записью справочника моделей "
            "(POST /api/models)."
        )
    if unknown:
        problems.append(
            f"Неизвестные секреты для типа {provider_type}: {', '.join(unknown)}. "
            f"Допустимы: {', '.join(allowed)}."
        )
    return problems


def _parse_yandex(secrets: Mapping[str, str], problems: list[str]) -> YandexSettings | None:
    """Разбор повторяет чтение тех же переменных из env-файла в xip: значения обрезаются, а в
    закрытом ключе экранированные переносы строк заменяются настоящими."""
    folder_id = secrets.get("YANDEX_FOLDER_ID", "").strip()
    static_token = secrets.get("YANDEX_IAM_TOKEN", "").strip()
    key_id = secrets.get("YANDEX_KEY_ID", "").strip()
    service_account_id = secrets.get("YANDEX_SERVICE_ACCOUNT_ID", "").strip()
    private_key = secrets.get("YANDEX_PRIVATE_KEY", "").replace("\\n", "\n").strip()
    base_url = secrets.get("YANDEX_BASE_URL", "").strip() or YANDEX_BASE_URL

    if folder_id == "":
        problems.append(
            "YANDEX_FOLDER_ID не задан. Короткие идентификаторы моделей справочника дополняются "
            "им до URI вида gpt://<каталог>/<модель>."
        )
    _check_url("YANDEX_BASE_URL", base_url, problems)

    auth: ServiceAccountKey | str | None = None
    if static_token != "":
        auth = static_token
    elif key_id != "" and service_account_id != "" and private_key != "":
        key = ServiceAccountKey(key_id, service_account_id, private_key)
        # Пригодность ключа проверяется сразу, до первого обращения к IAM: подпись выполняется
        # на месте и сетевого обмена не требует.
        try:
            sign_jwt(key)
        except YandexAuthError as error:
            problems.append(str(error))
        auth = key
    else:
        missing = [
            name
            for name, value in (
                ("YANDEX_KEY_ID", key_id),
                ("YANDEX_SERVICE_ACCOUNT_ID", service_account_id),
                ("YANDEX_PRIVATE_KEY", private_key),
            )
            if value == ""
        ]
        problems.append(
            "Не настроена аутентификация в Yandex Cloud. Задайте либо YANDEX_IAM_TOKEN, либо "
            f"ключ сервисного аккаунта целиком — не хватает: {', '.join(missing)}."
        )

    if folder_id == "" or auth is None:
        return None
    return YandexSettings(folder_id=folder_id, base_url=base_url.rstrip("/"), auth=auth)


def _parse_openai(
    secrets: Mapping[str, str], problems: list[str]
) -> OpenAICompatibleSettings | None:
    base_url = secrets.get("OPENAI_BASE_URL", "").strip()
    api_key = secrets.get("OPENAI_API_KEY", "").strip()
    if base_url == "":
        problems.append("OPENAI_BASE_URL не задан. Пример: http://localhost:11434/v1.")
        return None
    _check_url("OPENAI_BASE_URL", base_url, problems)
    return OpenAICompatibleSettings(base_url=base_url.rstrip("/"), api_key=api_key or None)


def _check_url(name: str, value: str, problems: list[str]) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or parsed.netloc == "":
        problems.append(f"{name}: ожидается адрес со схемой http или https, получено «{value}».")
