"""Справочник провайдеров моделей.

Провайдер — источник моделей с OpenAI-совместимым API: Yandex AI Studio либо сервер моделей,
например локальный vLLM или Ollama. Карточка провайдера хранит его тип и секреты; записи
справочника моделей ссылаются на карточку по имени.

Секреты передаются одним объектом с теми же ключами, что и переменные env-файла. Допустимые
ключи определяются типом провайдера:

- `yandex` — как в env-файле xip: `YANDEX_FOLDER_ID` обязателен; аутентификация задаётся
  ключом сервисного аккаунта (`YANDEX_KEY_ID`, `YANDEX_SERVICE_ACCOUNT_ID`,
  `YANDEX_PRIVATE_KEY`) либо готовым `YANDEX_IAM_TOKEN`; `YANDEX_BASE_URL` необязателен;
- `openai_compatible` — `OPENAI_BASE_URL` обязателен, `OPENAI_API_KEY` необязателен.

В ответах API значения собственно секретов — ключа, токена — заменены маской `SECRET_MASK`,
а остальные значения, например каталог и адрес, показываются как есть.
"""

from typing import Annotated, Literal

from pydantic import Field

from .base import ApiModel

PROVIDER_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"
"""Ограничение имени карточки. Имя задаётся при создании и не меняется: по нему на карточку
ссылаются записи справочника моделей."""

SECRET_MASK = "••••••"
"""Значение секрета в ответах API. В запросе правки означает «оставить прежнее значение»."""

ProviderType = Literal["yandex", "openai_compatible"]

ProviderCheckStatus = Literal["unknown", "ok", "unsatisfied", "unreachable"]
"""Состояние проверки карточки.

`unsatisfied` — провайдер ответил отказом, устранимым правкой карточки: неверные секреты,
каталог или адрес. `unreachable` — провайдер не ответил. Исправляются они в разных местах,
поэтому различаются.
"""

Secrets = dict[str, Annotated[str, Field(max_length=20_000)]]


class Provider(ApiModel):
    """Карточка провайдера."""

    id: str
    name: str
    title: str | None
    type: ProviderType
    secrets: dict[str, str]
    """Заданные секреты; значения собственно секретов заменены маской."""
    check_status: ProviderCheckStatus
    last_check_at: str | None
    last_check_message: str | None
    created_at: str
    updated_at: str


class CreateProviderRequest(ApiModel):
    name: str = Field(pattern=PROVIDER_NAME_PATTERN)
    title: str | None = Field(default=None, max_length=200)
    type: ProviderType
    secrets: Secrets = Field(default_factory=dict, max_length=20)


class UpdateProviderRequest(ApiModel):
    """Правка карточки. Имени в ней нет: оно не меняется.

    Секреты, если переданы, заменяют прежние целиком. Значение, равное маске, означает
    «оставить прежнее значение этого ключа»: так карточку можно получить, поправить один
    секрет и отправить обратно, не зная остальных.
    """

    title: str | None = Field(default=None, max_length=200)
    type: ProviderType | None = None
    secrets: Secrets | None = Field(default=None, max_length=20)


class ProviderListResponse(ApiModel):
    providers: list[Provider]


class ModelReference(ApiModel):
    id: str
    identifier: str


class ProviderInUseError(ApiModel):
    """Отказ в удалении карточки, на которую ссылаются модели справочника: тело ответа с кодом
    409."""

    status_code: Literal[409] = 409
    error: str
    message: str
    models: list[ModelReference]


class ProviderModel(ApiModel):
    """Модель из перечня провайдера."""

    identifier: str
    """Короткое имя: у Yandex — без префикса каталога, если каталог совпадает с настроенным."""
    full_id: str
    """Полный идентификатор, как его вернул провайдер."""
    vendor: str
    """Производитель из поля `owned_by`."""


class ProviderModelsResponse(ApiModel):
    models: list[ProviderModel]
    fetched_at: str | None
    """Момент, на который перечень актуален. Пусто, если получить его не удалось."""
    error: str | None
    """Причина, по которой перечень недоступен."""
