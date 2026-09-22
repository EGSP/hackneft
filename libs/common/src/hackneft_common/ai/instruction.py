"""Справочник инструкций.

Инструкция — именованный текст о том, как действовать в определённой ситуации. Карточка
агента ссылается на инструкции по идентификаторам, и сессия, созданная по карточке, получает
их тексты в системном промпте.
"""

from pydantic import Field

from .base import ApiModel

INSTRUCTION_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
"""Идентификатор инструкции: строчные латинские буквы и цифры, группы разделены дефисом."""


class Instruction(ApiModel):
    id: str
    """Идентификатор инструкции. Задаётся при создании и не меняется: по нему на инструкцию
    ссылаются карточки агентов."""
    title: str
    description: str = ""
    text: str
    created_at: str
    updated_at: str


class CreateInstructionRequest(ApiModel):
    id: str = Field(min_length=1, max_length=60, pattern=INSTRUCTION_ID_PATTERN)
    description: str = Field(default="", max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class UpdateInstructionRequest(ApiModel):
    """Правка инструкции. Идентификатора в ней нет: он не меняется."""

    description: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    text: str | None = Field(default=None, min_length=1, max_length=20_000)


class InstructionListResponse(ApiModel):
    instructions: list[Instruction]
    """Инструкции, упорядоченные по названию."""
