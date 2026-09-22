import asyncio
import sqlite3
from pathlib import Path

from sqlalchemy import select

from hackneft_ai.agents.seed import CAUSE_IDS, LEGACY_IDS, seed_defaults
from hackneft_ai.agents.service import AgentDirectory
from hackneft_ai.db.database import Database
from hackneft_ai.db.schema import AgentRow, CatalogMigrationRow, InstructionRow
from hackneft_ai.instructions.service import InstructionDirectory
from hackneft_common.ai import CreateInstructionRequest, UpdateInstructionRequest


def test_existing_catalog_migration_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "ai.db")
        try:
            await db.create_schema()
            async with db.write() as tx:
                for key in LEGACY_IDS:
                    tx.add(InstructionRow(id=key, title=key, text="Старая инструкция"))
                tx.add(InstructionRow(id="custom", title="Особая", text="Свой текст"))
                tx.add(
                    AgentRow(
                        id="advisor",
                        name="Советник",
                        description="Старая",
                        system_prompt="Прежний промпт",
                        model="chosen-model",
                        instructions=["mode-selection", "custom"],
                    )
                )
            await seed_defaults(db)
            instructions = InstructionDirectory(db)
            agents = AgentDirectory(db, instructions)
            catalog = await instructions.list_instructions()
            assert len(catalog) == 9  # Восемь штатных и пользовательская.
            assert set(CAUSE_IDS) <= {row.id for row in catalog}
            advisor = await agents.require("advisor")
            assert advisor.model == "chosen-model"
            assert advisor.instructions == [*CAUSE_IDS, "custom"]
            assert (await agents.require("protection")).instructions[-1] == "protection-mode"
            assert (await agents.require("production")).instructions[-1] == "production-mode"
            async with db.read() as tx:
                archive = await tx.get(CatalogMigrationRow, "six-reasons-v1")
                assert archive is not None
                assert archive.snapshot["agents"][0]["system_prompt"] == "Прежний промпт"
                assert len(archive.snapshot["instructions"]) == 13
            await instructions.update(
                "sulfur-risk",
                UpdateInstructionRequest(
                    description="  Новое описание  ", text="Изменено оператором"
                ),
            )
            await seed_defaults(db)
            edited = await instructions.require("sulfur-risk")
            assert edited.description == "Новое описание"
            assert edited.text == "Изменено оператором"
            prompt = await agents.session_prompt(await agents.require("advisor"))
            assert "Изменено оператором" in prompt
        finally:
            await db.close()

    asyncio.run(scenario())


def test_custom_agent_keeps_referenced_legacy_instruction(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "ai.db")
        try:
            await db.create_schema()
            async with db.write() as tx:
                tx.add(InstructionRow(id="sulfur-norm", title="Норма", text="Особый текст"))
                tx.add(
                    AgentRow(
                        id="custom",
                        name="Свой",
                        system_prompt="Свой",
                        model="custom-model",
                        instructions=["sulfur-norm"],
                    )
                )
            await seed_defaults(db)
            async with db.read() as tx:
                assert await tx.get(InstructionRow, "sulfur-norm") is not None
                agent = await tx.get(AgentRow, "custom")
                assert agent is not None and agent.instructions == ["sulfur-norm"]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_description_added_to_old_database_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE instructions (
            id VARCHAR(60) PRIMARY KEY, title VARCHAR(200) NOT NULL, text VARCHAR NOT NULL,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)""")
        connection.execute("""INSERT INTO instructions VALUES
            ('existing', 'Название', 'Текст', '2026-09-22 00:00:00', '2026-09-22 00:00:00')""")

    async def scenario() -> None:
        db = Database(path)
        try:
            await db.create_schema()
            directory = InstructionDirectory(db)
            assert (await directory.require("existing")).description == ""
            await directory.create(
                CreateInstructionRequest(
                    id="new", title="Новая", description="  Причина  ", text="Действия"
                )
            )
            assert (await directory.require("new")).description == "Причина"
            await directory.update("new", UpdateInstructionRequest(description=""))
            assert (await directory.require("new")).description == ""
            async with db.read() as tx:
                assert len(list(await tx.scalars(select(InstructionRow)))) == 2
        finally:
            await db.close()

    asyncio.run(scenario())
