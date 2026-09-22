"""Запуск агентов из агента: перечень карточек и дочерняя сессия с ожиданием итога.

Инструменты замкнуты на сессию, из которой вызваны: дочерняя сессия создаётся с её
идентификатором родителем, поэтому журнал родителя получает событие о порождении, а
прерывание родителя прерывает и потомков.

Вложенность ограничена одним уровнем: инструменты предлагаются только корневой сессии. Иначе
модель могла бы запускать агентов рекурсивно без предела, расходуя токены лавинообразно.
"""

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import select

from hackneft_common.ai import CreateSessionRequest

from ..agents.service import AgentDirectory
from ..core.tool import AgentTool, AnyAgentTool, ToolFailure, model_input
from ..db.database import Database
from ..db.schema import SessionRow
from ..errors import NotFoundError

if TYPE_CHECKING:
    from ..sessions.runner import AgentRunner
    from ..sessions.service import SessionsService


LIST_AGENTS = "list_agents"
RUN_AGENT = "run_agent"
AGENT_TOOL_NAMES = (LIST_AGENTS, RUN_AGENT)


class _NoArguments(BaseModel):
    pass


class _RunAgentArguments(BaseModel):
    agent_id: str = Field(description="Идентификатор агента из list_agents")
    task: str = Field(
        min_length=1,
        max_length=20_000,
        description="Задача агенту: что оценить и что вернуть. Коротко и конкретно",
    )
    include_input: bool = Field(
        default=True,
        description=(
            "Передать агенту исходные данные текущей сессии дословно. Не пересказывай данные "
            "в task: так числа дойдут без искажений"
        ),
    )


class AgentTools:
    """Инструменты `list_agents` и `run_agent`.

    Служба сессий и служба ходов сами зависят от набора инструментов, поэтому связываются
    с этим классом после создания методом `bind`.
    """

    def __init__(self, db: Database, agents: AgentDirectory) -> None:
        self._db = db
        self._agents = agents
        self._sessions: SessionsService | None = None
        self._runner: AgentRunner | None = None
        self._background: set[asyncio.Task[bool]] = set()

    def bind(self, sessions: "SessionsService", runner: "AgentRunner") -> None:
        self._sessions = sessions
        self._runner = runner

    async def for_session(self, session_id: str) -> list[AnyAgentTool]:
        async with self._db.read() as session:
            parent_id = await session.scalar(
                select(SessionRow.parent_id).where(SessionRow.id == session_id)
            )
        if parent_id is not None:
            return []
        return self.tools(session_id)

    def tools(self, session_id: str) -> list[AnyAgentTool]:
        async def list_agents(_: _NoArguments) -> object:
            agents = await self._agents.list_agents()
            return {
                "agents": [
                    {"id": agent.id, "name": agent.name, "description": agent.description}
                    for agent in agents
                ]
            }

        async def run_agent(arguments: _RunAgentArguments) -> object:
            return await self._run(session_id, arguments)

        return [
            AgentTool(
                name=LIST_AGENTS,
                description="Возвращает агентов, которых можно запустить: id, имя и назначение.",
                input=model_input(_NoArguments),
                execute=list_agents,
            ),
            AgentTool(
                name=RUN_AGENT,
                description=(
                    "Запускает агента дочерней сессией, дожидается завершения и возвращает его "
                    "итог. Агенты выполняются по одному: следующий вызов начинай после итога "
                    "предыдущего, если ему нужен этот итог."
                ),
                input=model_input(_RunAgentArguments),
                execute=run_agent,
            ),
        ]

    async def _run(self, session_id: str, arguments: _RunAgentArguments) -> object:
        sessions, runner = self._sessions, self._runner
        if sessions is None or runner is None:
            raise RuntimeError("AgentTools не связан со службой сессий")
        try:
            agent = await self._agents.require(arguments.agent_id)
        except NotFoundError as error:
            raise ToolFailure(
                f"Агента «{arguments.agent_id}» нет", "Вызови list_agents и выбери id из перечня."
            ) from error

        data = None
        if arguments.include_input:
            async with self._db.read() as session:
                data = await session.scalar(
                    select(SessionRow.input).where(SessionRow.id == session_id)
                )

        child = await sessions.create(
            CreateSessionRequest(
                kind="agent",
                parent_id=session_id,
                agent=agent.id,
                title=agent.name,
                task=arguments.task,
                input=data,
            )
        )
        try:
            await runner.wait(child.id)
        except asyncio.CancelledError:
            # Ход родителя прерван: дочерний агент больше никому не нужен. Прерывание идёт
            # отдельной задачей, потому что текущая уже отменяется.
            task = asyncio.get_running_loop().create_task(sessions.interrupt(child.id))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            raise

        final = await sessions.require(child.id)
        if final.status != "completed":
            raise ToolFailure(
                f"Агент «{agent.name}» не завершил задачу: {final.failure_message or final.status}",
                "Продолжай без его итога и явно отметь это в ответе.",
            )
        # Вызывающему агенту отдаётся только итог: журнал и рассуждения дочерней сессии в его
        # контекст не попадают. Связь сессий сервис хранит сам (parent_id).
        return {"agent": agent.id, "result": final.result}
