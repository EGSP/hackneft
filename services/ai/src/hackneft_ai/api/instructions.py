"""API справочника инструкций."""

from fastapi import APIRouter

from hackneft_common.ai import (
    AcceptedResponse,
    CreateInstructionRequest,
    Instruction,
    InstructionListResponse,
    UpdateInstructionRequest,
)

from .deps import ServicesDep, errors

router = APIRouter(prefix="/api/instructions", tags=["instructions"])


@router.get("", summary="Инструкции")
async def list_instructions(services: ServicesDep) -> InstructionListResponse:
    """Инструкции, упорядоченные по названию."""
    return InstructionListResponse(instructions=await services.instructions.list_instructions())


@router.post("", summary="Добавление инструкции", responses=errors(409))
async def create_instruction(body: CreateInstructionRequest, services: ServicesDep) -> Instruction:
    return await services.instructions.create(body)


@router.get("/{instruction_id}", summary="Инструкция", responses=errors(404))
async def get_instruction(instruction_id: str, services: ServicesDep) -> Instruction:
    return await services.instructions.require(instruction_id)


@router.patch("/{instruction_id}", summary="Правка инструкции", responses=errors(404))
async def update_instruction(
    instruction_id: str, body: UpdateInstructionRequest, services: ServicesDep
) -> Instruction:
    """Правка инструкции. Созданные ранее сессии сохраняют прежний текст."""
    return await services.instructions.update(instruction_id, body)


@router.delete("/{instruction_id}", summary="Удаление инструкции", responses=errors(404, 409))
async def remove_instruction(instruction_id: str, services: ServicesDep) -> AcceptedResponse:
    """Удаление инструкции. Пока она закреплена за агентами, запрос отклоняется с кодом 409."""
    await services.instructions.remove(instruction_id)
    return AcceptedResponse(accepted=True)
