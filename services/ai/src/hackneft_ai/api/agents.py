"""API справочника агентов."""

from fastapi import APIRouter

from hackneft_common.ai import (
    AcceptedResponse,
    Agent,
    AgentListResponse,
    CreateAgentRequest,
    UpdateAgentRequest,
)

from .deps import ServicesDep, errors

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", summary="Карточки агентов")
async def list_agents(services: ServicesDep) -> AgentListResponse:
    return AgentListResponse(agents=await services.agents.list_agents())


@router.post("", summary="Добавление агента", responses=errors(409))
async def create_agent(body: CreateAgentRequest, services: ServicesDep) -> Agent:
    """Добавление карточки. Ссылка на модель проверяется при создании сессии, а не здесь."""
    return await services.agents.create(body)


@router.get("/{agent_id}", summary="Карточка агента", responses=errors(404))
async def get_agent(agent_id: str, services: ServicesDep) -> Agent:
    return await services.agents.require(agent_id)


@router.patch("/{agent_id}", summary="Правка карточки агента", responses=errors(404))
async def update_agent(agent_id: str, body: UpdateAgentRequest, services: ServicesDep) -> Agent:
    """Правка карточки. Созданные по ней сессии сохраняют прежний системный промпт."""
    return await services.agents.update(agent_id, body)


@router.delete("/{agent_id}", summary="Удаление агента", responses=errors(404))
async def remove_agent(agent_id: str, services: ServicesDep) -> AcceptedResponse:
    await services.agents.remove(agent_id)
    return AcceptedResponse(accepted=True)
