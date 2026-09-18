"""Инструменты, написанные внутри сервиса.

Кубики и случайное число нужны для проверки вызова инструментов на конкретной модели: их
результат нельзя угадать, поэтому модель обязана его запросить. Заметки хранят состояние
между ходами и показывают отказ инструмента как штатный исход.
"""

import random

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update

from ..core.tool import AgentTool, AnyAgentTool, ToolFailure, model_input, tool_failure
from ..db.database import Database
from ..db.schema import SessionRow

MAX_NOTES = 3
"""Предел объёма заметок: малое значение нужно, чтобы модель встречала отказ инструмента."""

_STORAGE_HINT = (
    "Хранилище сервиса недоступно, и повтор того же вызова даст тот же результат. "
    "Продолжай без заметок либо сообщи, что сохранить их не удалось."
)


class _DiceArguments(BaseModel):
    count: int = Field(default=1, ge=1, le=10, description="Сколько кубиков бросить")
    sides: int = Field(default=6, ge=2, le=100, description="Сколько граней у кубика")


class _RangeArguments(BaseModel):
    min: int = Field(description="Нижняя граница диапазона")
    max: int = Field(description="Верхняя граница диапазона")

    @model_validator(mode="after")
    def _ordered(self) -> "_RangeArguments":
        if self.min > self.max:
            raise ValueError("min не может быть больше max")
        return self


class _NoArguments(BaseModel):
    pass


class _NoteArguments(BaseModel):
    text: str = Field(min_length=1, description="Текст заметки, одна строка")


async def _roll_dice(arguments: _DiceArguments) -> object:
    rolls = [random.randint(1, arguments.sides) for _ in range(arguments.count)]
    return {"rolls": rolls, "sum": sum(rolls), "sides": arguments.sides}


async def _random_number(arguments: _RangeArguments) -> object:
    return {
        "value": random.randint(arguments.min, arguments.max),
        "min": arguments.min,
        "max": arguments.max,
    }


ROLL_DICE: AnyAgentTool = AgentTool(
    name="roll_dice",
    description=(
        "Бросает игральные кубики и возвращает выпавшие значения и их сумму. Результат "
        "случаен, предсказать его нельзя — его обязательно нужно получить вызовом."
    ),
    input=model_input(_DiceArguments),
    execute=_roll_dice,
)

RANDOM_NUMBER: AnyAgentTool = AgentTool(
    name="random_number",
    description=(
        "Возвращает случайное целое число в заданном диапазоне включительно. Результат "
        "случаен, его нужно получить вызовом, а не придумать."
    ),
    input=model_input(_RangeArguments),
    execute=_random_number,
)

_NO_ARGUMENTS = model_input(_NoArguments)
_NOTE_ARGUMENTS = model_input(_NoteArguments)


class NotesTools:
    """Заметки сессии. Инструменты замкнуты на сессию и создаются на каждый ход."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def for_session(self, session_id: str) -> list[AnyAgentTool]:
        async def read_notes(_: _NoArguments) -> object:
            lines = await self._read(session_id)
            return {"count": len(lines), "limit": MAX_NOTES, "lines": lines}

        async def write_note(arguments: _NoteArguments) -> object:
            lines = await self._read(session_id)
            if len(lines) >= MAX_NOTES:
                # Отказ инструмента — штатная ситуация. Текст написан так, чтобы модель поняла,
                # что делать дальше, а не повторяла тот же вызов.
                raise ToolFailure(
                    f"В заметках уже {len(lines)} строк из {MAX_NOTES}, добавить нельзя.",
                    "Вызови clear_notes, если старые записи больше не нужны, и повтори запись.",
                )
            line = " ".join(arguments.text.split())
            await self._write(session_id, [*lines, line], "Заметка не сохранена")
            return {"written": line, "count": len(lines) + 1, "limit": MAX_NOTES}

        async def clear_notes(_: _NoArguments) -> object:
            before = len(await self._read(session_id))
            await self._write(session_id, [], "Заметки сессии не очищены")
            return {"cleared": before}

        return [
            AgentTool(
                name="read_notes",
                description=(
                    "Читает заметки текущей сессии и возвращает их строки. Если заметок нет, "
                    "возвращает пустой список."
                ),
                input=_NO_ARGUMENTS,
                execute=read_notes,
            ),
            AgentTool(
                name="write_note",
                description=(
                    f"Дописывает одну строку в заметки сессии. Строк не может быть больше "
                    f"{MAX_NOTES}: если предел достигнут, вызов отклоняется и заметки нужно "
                    "сначала очистить."
                ),
                input=_NOTE_ARGUMENTS,
                execute=write_note,
            ),
            AgentTool(
                name="clear_notes",
                description="Полностью очищает заметки сессии. Операция необратима.",
                input=_NO_ARGUMENTS,
                execute=clear_notes,
            ),
        ]

    async def _read(self, session_id: str) -> list[str]:
        # Обращение к базе способно отказать, и отказ должен дойти до модели штатным исходом
        # с подсказкой, а не дефектом с текстом исключения.
        try:
            async with self._db.read() as session:
                notes = await session.scalar(
                    select(SessionRow.notes).where(SessionRow.id == session_id)
                )
        except Exception as error:
            raise tool_failure("Заметки сессии не прочитаны", _STORAGE_HINT, error) from error
        return [line for line in notes or [] if isinstance(line, str)]

    async def _write(self, session_id: str, lines: list[str], summary: str) -> None:
        try:
            async with self._db.write() as tx:
                await tx.execute(
                    update(SessionRow).where(SessionRow.id == session_id).values(notes=lines)
                )
        except Exception as error:
            raise tool_failure(summary, _STORAGE_HINT, error) from error
