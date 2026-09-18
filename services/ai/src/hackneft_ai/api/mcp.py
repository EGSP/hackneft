"""API справочника подключений MCP."""

from fastapi import APIRouter

from hackneft_common.ai import (
    AcceptedResponse,
    CreateMcpConnectionRequest,
    ImportMcpConnectionsRequest,
    McpConnection,
    McpConnectionListResponse,
    McpImportResponse,
    ToggleMcpConnectionRequest,
    UpdateMcpConnectionRequest,
)

from .deps import ServicesDep, errors

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("", summary="Подключения MCP")
async def list_connections(services: ServicesDep) -> McpConnectionListResponse:
    return McpConnectionListResponse(connections=await services.mcp.list_connections())


@router.post("", summary="Добавление подключения", responses=errors(400, 409))
async def create_connection(
    body: CreateMcpConnectionRequest, services: ServicesDep
) -> McpConnection:
    """Создание записи. Недостижимый сервер добавить нельзя: обнаружение выполняется до записи."""
    return await services.mcp.create(body)


@router.post("/import", summary="Импорт конфигурации mcpServers", responses=errors(400, 409))
async def import_connections(
    body: ImportMcpConnectionsRequest, services: ServicesDep
) -> McpImportResponse:
    """Импорт конфигурации в сложившемся формате MCP-клиентов. Объявлен до маршрутов с
    параметром пути: иначе `import` было бы разобрано как идентификатор записи."""
    created, skipped = await services.mcp.import_config(body.config_json)
    return McpImportResponse(created=created, skipped=skipped)


@router.get("/{connection_id}", summary="Подключение MCP", responses=errors(404))
async def get_connection(connection_id: str, services: ServicesDep) -> McpConnection:
    return await services.mcp.require(connection_id)


@router.patch("/{connection_id}", summary="Правка подключения", responses=errors(400, 404, 409))
async def update_connection(
    connection_id: str, body: UpdateMcpConnectionRequest, services: ServicesDep
) -> McpConnection:
    return await services.mcp.update(connection_id, body)


@router.delete("/{connection_id}", summary="Удаление подключения", responses=errors(404))
async def remove_connection(connection_id: str, services: ServicesDep) -> AcceptedResponse:
    await services.mcp.remove(connection_id)
    return AcceptedResponse(accepted=True)


@router.post(
    "/{connection_id}/check", summary="Обнаружение инструментов сервера", responses=errors(404)
)
async def check_connection(connection_id: str, services: ServicesDep) -> McpConnection:
    """Повторное обнаружение: подключение к серверу и запрос перечня инструментов."""
    return await services.mcp.check(connection_id)


@router.post(
    "/{connection_id}/toggle",
    summary="Включение и выключение подключения",
    responses=errors(400, 404),
)
async def toggle_connection(
    connection_id: str, body: ToggleMcpConnectionRequest, services: ServicesDep
) -> McpConnection:
    """Включение сопровождается обнаружением, выключение выполняется без обращения к серверу."""
    return await services.mcp.toggle(connection_id, body.enabled)
