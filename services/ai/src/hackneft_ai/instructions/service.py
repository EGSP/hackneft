"""Справочник инструкций.

Инструкция — именованный текст о том, как действовать в определённой ситуации. Карточки
агентов ссылаются на инструкции по идентификаторам. Сессия получает тексты инструкций при
создании, поэтому правка инструкции на созданные сессии не влияет.
"""

from collections.abc import Sequence

from sqlalchemy import select, update

from hackneft_common.ai import CreateInstructionRequest, Instruction, UpdateInstructionRequest

from ..db.database import Database
from ..db.schema import AgentRow, InstructionRow, iso
from ..errors import BadRequestError, ConflictError, NotFoundError


class InstructionDirectory:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_instructions(self) -> list[Instruction]:
        """Инструкции по названию. Порядок задаётся здесь, а не запросом: SQLite сравнивает
        строки побайтно, и буква «ё» оказалась бы после «я»."""
        async with self._db.read() as session:
            rows = (await session.scalars(select(InstructionRow))).all()
        return sorted((_to_instruction(row) for row in rows), key=lambda i: _collation(i.title))

    async def require(self, instruction_id: str) -> Instruction:
        async with self._db.read() as session:
            row = await session.get(InstructionRow, instruction_id)
        if row is None:
            raise NotFoundError(f"Инструкция «{instruction_id}» не найдена")
        return _to_instruction(row)

    async def require_all(self, instruction_ids: Sequence[str]) -> list[Instruction]:
        """Инструкции в порядке перечня. Отсутствующие перечисляются в отказе все сразу."""
        async with self._db.read() as session:
            rows = (
                await session.scalars(
                    select(InstructionRow).where(InstructionRow.id.in_(instruction_ids))
                )
            ).all()
        found = {row.id: row for row in rows}
        missing = [item for item in instruction_ids if item not in found]
        if missing:
            names = ", ".join(f"«{item}»" for item in missing)
            raise BadRequestError(f"Инструкции не найдены: {names}")
        return [_to_instruction(found[item]) for item in instruction_ids]

    async def create(self, request: CreateInstructionRequest) -> Instruction:
        async with self._db.write() as tx:
            if await tx.get(InstructionRow, request.id) is not None:
                raise ConflictError(f"Инструкция «{request.id}» уже существует")
            tx.add(
                InstructionRow(
                    id=request.id,
                    title=request.title.strip(),
                    text=request.text.strip(),
                    description=request.description.strip(),
                )
            )
        return await self.require(request.id)

    async def update(self, instruction_id: str, request: UpdateInstructionRequest) -> Instruction:
        await self.require(instruction_id)
        values: dict[str, str] = {}
        if request.title is not None:
            values["title"] = request.title.strip()
        if request.description is not None:
            values["description"] = request.description.strip()
        if request.text is not None:
            values["text"] = request.text.strip()
        if values:
            async with self._db.write() as tx:
                await tx.execute(
                    update(InstructionRow)
                    .where(InstructionRow.id == instruction_id)
                    .values(**values)
                )
        return await self.require(instruction_id)

    async def remove(self, instruction_id: str) -> None:
        """Удаление инструкции. Пока она закреплена за агентами, запрос отклоняется: иначе
        карточка молча лишилась бы части указаний."""
        await self.require(instruction_id)
        async with self._db.read() as session:
            agents = (await session.scalars(select(AgentRow).order_by(AgentRow.id))).all()
        holders = [agent.id for agent in agents if instruction_id in agent.instructions]
        if holders:
            names = ", ".join(f"«{agent}»" for agent in holders)
            raise ConflictError(
                f"Инструкция закреплена за агентами: {names}. Сначала открепите её."
            )
        async with self._db.write() as tx:
            row = await tx.get(InstructionRow, instruction_id)
            if row is not None:
                await tx.delete(row)


def _collation(title: str) -> str:
    return title.casefold().replace("ё", "е")


def _to_instruction(row: InstructionRow) -> Instruction:
    return Instruction(
        id=row.id,
        title=row.title,
        text=row.text,
        description=row.description,
        created_at=iso(row.created_at),
        updated_at=iso(row.updated_at),
    )
