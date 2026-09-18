"""API справочника провайдеров моделей."""

from fastapi import APIRouter

from hackneft_common.ai import (
    AcceptedResponse,
    CreateProviderRequest,
    Provider,
    ProviderListResponse,
    ProviderModelsResponse,
    UpdateProviderRequest,
)

from .deps import ServicesDep

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
async def list_providers(services: ServicesDep) -> ProviderListResponse:
    """Карточки провайдеров. Значения собственно секретов заменены маской."""
    return ProviderListResponse(providers=await services.providers.list_providers())


@router.post("")
async def create_provider(body: CreateProviderRequest, services: ServicesDep) -> Provider:
    """Добавление карточки. Секреты проверяются до сохранения, связь с провайдером — после:
    её исход записывается в карточку."""
    return await services.providers.create(body)


@router.get("/{provider_id}")
async def get_provider(provider_id: str, services: ServicesDep) -> Provider:
    return await services.providers.require(provider_id)


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: str, body: UpdateProviderRequest, services: ServicesDep
) -> Provider:
    """Правка карточки. Секреты заменяются целиком; значение-маска сохраняет прежнее значение
    ключа. Имя карточки не меняется."""
    return await services.providers.update(provider_id, body)


@router.delete("/{provider_id}")
async def remove_provider(provider_id: str, services: ServicesDep) -> AcceptedResponse:
    """Удаление карточки. Пока на неё ссылаются модели справочника, запрос отклоняется с кодом
    409, и тело отказа перечисляет эти модели в поле `models`."""
    await services.providers.remove(provider_id)
    return AcceptedResponse(accepted=True)


@router.post("/{provider_id}/check")
async def check_provider(provider_id: str, services: ServicesDep) -> Provider:
    """Проверка связи с провайдером запросом перечня моделей."""
    return await services.providers.check(provider_id)


@router.get("/{provider_id}/models")
async def list_provider_models(provider_id: str, services: ServicesDep) -> ProviderModelsResponse:
    """Перечень моделей провайдера — источник вариантов при добавлении в справочник моделей."""
    return await services.providers.models(provider_id)
