"""Справочник провайдеров моделей.

Карточка хранит тип провайдера и его секреты; по карточке собирается провайдер, которым
пользуются ходы. Справочник — единственный источник провайдеров: в конфигурации сервиса их нет.

Секреты проверяются при сохранении, до обращения к провайдеру: неизвестный ключ, отсутствующий
каталог или непригодный закрытый ключ отклоняются сразу. Связь с провайдером проверяется после
сохранения запросом перечня моделей, и её исход записывается в карточку: недоступный
провайдер добавить можно, а причина видна в карточке.
"""

import logging

import httpx2
from sqlalchemy import select, update

from hackneft_common.ai import (
    CreateProviderRequest,
    ModelReference,
    Provider,
    ProviderCheckStatus,
    ProviderInUseError,
    ProviderModelsResponse,
    ProviderType,
    UpdateProviderRequest,
)

from ..db.database import Database
from ..db.schema import LlmModelRow, ProviderRow, iso, utc_now
from ..errors import BadRequestError, ConflictError, NotFoundError
from .availability import ModelAvailabilityChecker
from .base import ModelProvider, ProviderModelsOk
from .kinds import SecretsError, build_provider, masked, merge
from .registry import ProviderRegistry

logger = logging.getLogger(__name__)

_TYPES: dict[str, ProviderType] = {"yandex": "yandex", "openai_compatible": "openai_compatible"}
_STATUSES: dict[str, ProviderCheckStatus] = {
    "ok": "ok",
    "unsatisfied": "unsatisfied",
    "unreachable": "unreachable",
}


class ProviderDirectory:
    def __init__(
        self,
        db: Database,
        registry: ProviderRegistry,
        http: httpx2.AsyncClient,
        availability: ModelAvailabilityChecker,
    ) -> None:
        self._db = db
        self._registry = registry
        self._http = http
        self._availability = availability

    async def load(self) -> None:
        """Собирает провайдеров по всем карточкам при запуске сервиса."""
        async with self._db.read() as session:
            rows = (await session.scalars(select(ProviderRow))).all()
        for row in rows:
            try:
                self._registry.put(self._build(row.name, _type(row.type), row.secrets))
            except BadRequestError as error:
                logger.error("карточка провайдера «%s» не собрана: %s", row.name, error.message)

    async def list_providers(self) -> list[Provider]:
        async with self._db.read() as session:
            rows = (await session.scalars(select(ProviderRow).order_by(ProviderRow.name))).all()
        return [_to_provider(row) for row in rows]

    async def require(self, provider_id: str) -> Provider:
        return _to_provider(await self._row(provider_id))

    async def create(self, request: CreateProviderRequest) -> Provider:
        async with self._db.read() as session:
            existing = await session.scalar(
                select(ProviderRow.id).where(ProviderRow.name == request.name)
            )
        if existing is not None:
            raise ConflictError(f'Провайдер "{request.name}" уже добавлен')

        provider = self._build(request.name, request.type, request.secrets)
        async with self._db.write() as tx:
            row = ProviderRow(
                name=request.name,
                title=request.title,
                type=request.type,
                secrets=dict(request.secrets),
            )
            tx.add(row)
            await tx.flush()
            provider_id = row.id
        self._registry.put(provider)
        return await self._check_and_refresh(provider_id)

    async def update(self, provider_id: str, request: UpdateProviderRequest) -> Provider:
        """Правка карточки. Секреты, если переданы, заменяют прежние целиком; значение, равное
        маске, сохраняет прежнее значение ключа. Смена типа требует секретов нового типа."""
        row = await self._row(provider_id)
        provider_type = request.type or _type(row.type)
        try:
            secrets = (
                row.secrets if request.secrets is None else merge(row.secrets, request.secrets)
            )
        except SecretsError as error:
            raise BadRequestError(" ".join(error.problems)) from error

        provider = self._build(row.name, provider_type, secrets)
        values: dict[str, object] = {"type": provider_type, "secrets": secrets}
        if "title" in request.model_fields_set:
            values["title"] = request.title
        async with self._db.write() as tx:
            await tx.execute(
                update(ProviderRow).where(ProviderRow.id == provider_id).values(**values)
            )
        self._registry.put(provider)
        return await self._check_and_refresh(provider_id)

    async def remove(self, provider_id: str) -> None:
        """Удаление карточки. Пока на неё ссылаются модели справочника, карточка не
        удаляется, и отказ перечисляет эти модели: провайдер модели не меняется, поэтому их
        нужно удалить первыми."""
        row = await self._row(provider_id)
        async with self._db.read() as session:
            models = (
                await session.execute(
                    select(LlmModelRow.id, LlmModelRow.identifier)
                    .where(LlmModelRow.provider == row.name)
                    .order_by(LlmModelRow.identifier)
                )
            ).all()
        if models:
            references = [ModelReference(id=model_id, identifier=name) for model_id, name in models]
            message = (
                f"Провайдер «{row.name}» используется моделями справочника: "
                f"{', '.join(reference.identifier for reference in references)}. "
                "Сначала удалите эти модели."
            )
            body = ProviderInUseError(error="Conflict", message=message, models=references)
            raise ConflictError(message, extra={"models": body.model_dump(mode="json")["models"]})

        async with self._db.write() as tx:
            current = await tx.get(ProviderRow, provider_id)
            if current is not None:
                await tx.delete(current)
        self._registry.remove(row.name)

    async def check(self, provider_id: str) -> Provider:
        """Проверка связи: запрос перечня моделей с учётными данными карточки."""
        row = await self._row(provider_id)
        provider = self._registry.get(row.name)
        if provider is None:
            status: ProviderCheckStatus = "unsatisfied"
            message = "Провайдер не собран по карточке: исправьте секреты."
        else:
            listing = await provider.list_models(force=True)
            if isinstance(listing, ProviderModelsOk):
                status, message = "ok", f"Получено моделей: {len(listing.models)}."
            else:
                status, message = listing.kind, listing.error
        async with self._db.write() as tx:
            await tx.execute(
                update(ProviderRow)
                .where(ProviderRow.id == provider_id)
                .values(check_status=status, last_check_at=utc_now(), last_check_message=message)
            )
        return await self.require(provider_id)

    async def models(self, provider_id: str) -> ProviderModelsResponse:
        """Перечень моделей провайдера — источник вариантов при добавлении в справочник."""
        row = await self._row(provider_id)
        listing = await self._registry.require(row.name).list_models()
        if isinstance(listing, ProviderModelsOk):
            return ProviderModelsResponse(
                models=list(listing.models), fetched_at=iso(listing.fetched_at), error=None
            )
        return ProviderModelsResponse(models=[], fetched_at=None, error=listing.error)

    async def _check_and_refresh(self, provider_id: str) -> Provider:
        provider = await self.check(provider_id)
        # Доступность моделей провайдера выясняется сразу, а не по таймеру: перечень только что
        # получен и берётся из кеша провайдера.
        await self._availability.refresh()
        return provider

    def _build(
        self, name: str, provider_type: ProviderType, secrets: dict[str, str]
    ) -> ModelProvider:
        try:
            return build_provider(name, provider_type, secrets, self._http)
        except SecretsError as error:
            raise BadRequestError(" ".join(error.problems)) from error

    async def _row(self, provider_id: str) -> ProviderRow:
        async with self._db.read() as session:
            row = await session.get(ProviderRow, provider_id)
        if row is None:
            raise NotFoundError(f"Провайдер {provider_id} не найден")
        return row


def _type(value: str) -> ProviderType:
    return _TYPES.get(value, "openai_compatible")


def _to_provider(row: ProviderRow) -> Provider:
    provider_type = _type(row.type)
    return Provider(
        id=row.id,
        name=row.name,
        title=row.title,
        type=provider_type,
        secrets=masked(provider_type, row.secrets),
        check_status=_STATUSES.get(row.check_status, "unknown"),
        last_check_at=None if row.last_check_at is None else iso(row.last_check_at),
        last_check_message=row.last_check_message,
        created_at=iso(row.created_at),
        updated_at=iso(row.updated_at),
    )
