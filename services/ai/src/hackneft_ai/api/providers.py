"""API справочника провайдеров моделей."""

from fastapi import APIRouter

from hackneft_common.ai import (
    AcceptedResponse,
    CreateProviderRequest,
    Provider,
    ProviderInUseError,
    ProviderListResponse,
    ProviderModelsResponse,
    UpdateProviderRequest,
)

from .deps import ServicesDep, errors

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("", summary="Карточки провайдеров")
async def list_providers(services: ServicesDep) -> ProviderListResponse:
    """Карточки провайдеров. Значения собственно секретов заменены маской."""
    return ProviderListResponse(providers=await services.providers.list_providers())


@router.post("", summary="Добавление провайдера", responses=errors(400, 409))
async def create_provider(body: CreateProviderRequest, services: ServicesDep) -> Provider:
    """Добавление карточки. Секреты проверяются до сохранения, связь с провайдером — после:
    её исход записывается в карточку."""
    return await services.providers.create(body)


@router.get("/{provider_id}", summary="Карточка провайдера", responses=errors(404))
async def get_provider(provider_id: str, services: ServicesDep) -> Provider:
    return await services.providers.require(provider_id)


@router.patch("/{provider_id}", summary="Правка карточки провайдера", responses=errors(400, 404))
async def update_provider(
    provider_id: str, body: UpdateProviderRequest, services: ServicesDep
) -> Provider:
    """Правка карточки. Секреты заменяются целиком; значение-маска сохраняет прежнее значение
    ключа. Имя карточки не меняется."""
    return await services.providers.update(provider_id, body)


@router.delete(
    "/{provider_id}",
    summary="Удаление карточки провайдера",
    responses=errors(404, 409, conflict=ProviderInUseError),
)
async def remove_provider(provider_id: str, services: ServicesDep) -> AcceptedResponse:
    """Удаление карточки. Пока на неё ссылаются модели справочника, запрос отклоняется с кодом
    409, и тело отказа перечисляет эти модели в поле `models`."""
    await services.providers.remove(provider_id)
    return AcceptedResponse(accepted=True)


@router.post(
    "/{provider_id}/check", summary="Проверка связи с провайдером", responses=errors(400, 404)
)
async def check_provider(provider_id: str, services: ServicesDep) -> Provider:
    """Проверка связи с провайдером запросом перечня моделей."""
    return await services.providers.check(provider_id)


@router.get("/{provider_id}/models", summary="Модели провайдера", responses=errors(404))
async def list_provider_models(provider_id: str, services: ServicesDep) -> ProviderModelsResponse:
    """Перечень моделей провайдера — источник вариантов при добавлении в справочник моделей."""
    return await services.providers.models(provider_id)
