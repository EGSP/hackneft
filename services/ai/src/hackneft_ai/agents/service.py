"""Справочник агентов.

Карточка задаёт системный промпт, закреплённые инструкции и модель сессии. Сессия копирует
промпт карточки вместе с текстами инструкций при создании, поэтому правка и удаление карточки
или инструкций на созданные сессии не влияют. Ссылка на модель в карточке при сохранении не
проверяется: синоним может обозначать модели, которые ещё не добавлены, и разрешается в запись
только при создании сессии.
"""

from sqlalchemy import func, select, update

from hackneft_common.ai import Agent, CreateAgentRequest, UpdateAgentRequest

from ..db.database import Database
from ..db.schema import AgentRow, SessionRow, iso
from ..errors import ConflictError, NotFoundError
from ..instructions.service import InstructionDirectory


class AgentDirectory:
    def __init__(self, db: Database, instructions: InstructionDirectory) -> None:
        self._db = db
        self._instructions = instructions

    async def list_agents(self) -> list[Agent]:
        async with self._db.read() as session:
            rows = (await session.scalars(select(AgentRow).order_by(AgentRow.id))).all()
        return [_to_agent(row) for row in rows]

    async def is_empty(self) -> bool:
        async with self._db.read() as session:
            count = await session.scalar(select(func.count()).select_from(AgentRow))
        return not count

    async def require(self, agent_id: str) -> Agent:
        async with self._db.read() as session:
            row = await session.get(AgentRow, agent_id)
        if row is None:
            raise NotFoundError(f"Агент «{agent_id}» не найден")
        return _to_agent(row)

    async def create(self, request: CreateAgentRequest) -> Agent:
        instructions = _unique(request.instructions)
        await self._instructions.require_all(instructions)
        async with self._db.write() as tx:
            if await tx.get(AgentRow, request.id) is not None:
                raise ConflictError(f"Агент «{request.id}» уже существует")
            tx.add(
                AgentRow(
                    id=request.id,
                    name=request.name.strip(),
                    description=request.description.strip(),
                    system_prompt=request.system_prompt,
                    instructions=instructions,
                    model=request.model.strip(),
                )
            )
        return await self.require(request.id)

    async def update(self, agent_id: str, request: UpdateAgentRequest) -> Agent:
        await self.require(agent_id)
        values: dict[str, str | list[str]] = {}
        if request.name is not None:
            values["name"] = request.name.strip()
        if request.description is not None:
            values["description"] = request.description.strip()
        if request.system_prompt is not None:
            values["system_prompt"] = request.system_prompt
        if request.instructions is not None:
            instructions = _unique(request.instructions)
            await self._instructions.require_all(instructions)
            values["instructions"] = instructions
        if request.model is not None:
            values["model"] = request.model.strip()
        if values:
            async with self._db.write() as tx:
                await tx.execute(update(AgentRow).where(AgentRow.id == agent_id).values(**values))
        return await self.require(agent_id)

    async def remove(self, agent_id: str) -> None:
        """Удаление карточки. Сессии сохраняют скопированный промпт и модель, теряя только
        ссылку на карточку. Ссылка обнуляется явно: в базе, дополненной столбцом позже,
        внешнего ключа у него нет."""
        await self.require(agent_id)
        async with self._db.write() as tx:
            await tx.execute(
                update(SessionRow).where(SessionRow.agent_id == agent_id).values(agent_id=None)
            )
            row = await tx.get(AgentRow, agent_id)
            if row is not None:
                await tx.delete(row)

    async def session_prompt(self, agent: Agent) -> str:
        """Системный промпт сессии, созданной по карточке: промпт карточки и за ним тексты
        закреплённых инструкций в порядке перечня."""
        instructions = await self._instructions.require_all(agent.instructions)
        if not instructions:
            return agent.system_prompt
        sections = "\n\n".join(f"### {item.title}\n\n{item.text}" for item in instructions)
        return f"{agent.system_prompt.strip()}\n\n## Инструкции\n\n{sections}"


def _unique(items: list[str]) -> list[str]:
    """Перечень без повторов с сохранением порядка."""
    return list(dict.fromkeys(item.strip() for item in items))


def _to_agent(row: AgentRow) -> Agent:
    return Agent(
        id=row.id,
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        instructions=list(row.instructions),
        model=row.model,
        created_at=iso(row.created_at),
        updated_at=iso(row.updated_at),
    )
