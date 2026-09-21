"""Сборка служб сервиса и их остановка."""

import logging
from dataclasses import dataclass

import httpx2

from ..agents.seed import seed_defaults
from ..agents.service import AgentDirectory
from ..config import AppConfig
from ..db.database import Database
from ..instructions.service import InstructionDirectory
from ..mcp.client import McpClient
from ..mcp.directory import McpDirectory
from ..models.service import ModelDirectory
from ..providers.availability import ModelAvailabilityChecker
from ..providers.directory import ProviderDirectory
from ..providers.registry import ProviderRegistry
from ..sessions.bus import SessionEventBus
from ..sessions.journal import SessionJournal
from ..sessions.runner import AgentRunner
from ..sessions.service import SessionsService
from ..sessions.snapshots import RequestSnapshotStore
from ..telemetry.session_traces import SessionTraceRegistry
from ..tools.builtin import NotesTools
from ..tools.factory import ToolsFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Services:
    config: AppConfig
    db: Database
    http: httpx2.AsyncClient
    bus: SessionEventBus
    journal: SessionJournal
    snapshots: RequestSnapshotStore
    registry: ProviderRegistry
    providers: ProviderDirectory
    availability: ModelAvailabilityChecker
    models: ModelDirectory
    instructions: InstructionDirectory
    agents: AgentDirectory
    mcp: McpDirectory
    tools: ToolsFactory
    runner: AgentRunner
    sessions: SessionsService


async def start_services(config: AppConfig) -> Services:
    db = Database(config.database_path)
    await db.create_schema()
    http = httpx2.AsyncClient()

    registry = ProviderRegistry()
    availability = ModelAvailabilityChecker(db, registry)
    providers = ProviderDirectory(db, registry, http, availability)
    await providers.load()
    if not registry.names():
        logger.warning(
            "в справочнике нет ни одного провайдера моделей: добавьте его запросом "
            "POST /api/providers. Сессии создаются, но ход не запустится."
        )
    for name in registry.names():
        logger.info("провайдер %s собран по карточке справочника", name)

    bus = SessionEventBus()
    journal = SessionJournal(db, bus)
    snapshots = RequestSnapshotStore(db)
    models = ModelDirectory(db, registry, availability)
    instructions = InstructionDirectory(db)
    agents = AgentDirectory(db, instructions)
    mcp_client = McpClient(config.mcp)
    mcp = McpDirectory(db, mcp_client)
    tools = ToolsFactory(config.mcp, NotesTools(db), mcp, mcp_client)
    runner = AgentRunner(
        db=db,
        journal=journal,
        snapshots=snapshots,
        models=models,
        providers=registry,
        tools=tools,
        traces=SessionTraceRegistry(),
        agent=config.agent,
        tracing=config.tracing,
    )
    sessions = SessionsService(db, bus, journal, models, agents, runner)

    # Сверка выполняется до того, как сервис начинает принимать запросы: проверки по
    # состоянию сессии устаревшего значения не видят.
    await runner.reconcile_on_startup()
    await models.relink()
    await models.check_problems()
    await seed_defaults(agents, instructions)
    availability.start()

    return Services(
        config=config,
        db=db,
        http=http,
        bus=bus,
        journal=journal,
        snapshots=snapshots,
        registry=registry,
        providers=providers,
        availability=availability,
        models=models,
        instructions=instructions,
        agents=agents,
        mcp=mcp,
        tools=tools,
        runner=runner,
        sessions=sessions,
    )


async def stop_services(services: Services) -> None:
    await services.availability.stop()
    await services.runner.shutdown()
    await services.registry.aclose()
    await services.http.aclose()
    await services.db.close()
