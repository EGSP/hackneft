"""API справочника моделей."""

from fastapi import APIRouter

from hackneft_common.ai import (
    AcceptedResponse,
    CreateModelProfileRequest,
    ModelListResponse,
    ModelProfile,
    UpdateModelProfileRequest,
)

from .deps import ServicesDep, errors

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", summary="Справочник моделей")
async def list_models(services: ServicesDep) -> ModelListResponse:
    """Справочник вместе с сессиями, в которых сейчас идёт ход на каждой из моделей."""
    return ModelListResponse(models=await services.models.list_profiles())


@router.post("", summary="Добавление модели", responses=errors(400, 409))
async def create_model(body: CreateModelProfileRequest, services: ServicesDep) -> ModelProfile:
    """Добавление модели. Первая добавленная модель становится моделью по умолчанию."""
    return await services.models.create(body)


@router.post("/check", summary="Проверка доступности моделей")
async def check_models(services: ServicesDep) -> AcceptedResponse:
    """Немедленная перепроверка доступности всех записей справочника по перечням провайдеров."""
    updated = await services.availability.refresh(force=True)
    return AcceptedResponse(accepted=updated > 0)


@router.patch("/{model_id}", summary="Правка модели", responses=errors(404, 409))
async def update_model(
    model_id: str, body: UpdateModelProfileRequest, services: ServicesDep
) -> ModelProfile:
    """Правка признаков и пометки по умолчанию. Провайдер и идентификатор модели не меняются."""
    return await services.models.update(model_id, body)


@router.delete("/{model_id}", summary="Удаление модели", responses=errors(404, 409))
async def remove_model(model_id: str, services: ServicesDep) -> AcceptedResponse:
    """Удаление записи. Пока моделью исполняется ход, запрос отклоняется с кодом 409, и тело
    отказа перечисляет эти сессии в поле `sessions`."""
    await services.models.remove(model_id)
    return AcceptedResponse(accepted=True)
