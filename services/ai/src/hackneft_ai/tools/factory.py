"""Сборка набора инструментов под конкретную сессию.

Набор собирается на каждый ход, а не создаётся один раз: инструменты заметок замкнуты на свою
сессию, а состав подключений MCP меняется во время работы.
"""

import json
import logging
from collections.abc import Sequence

from hackneft_common.ai import ToolInfo

from ..config import McpConfig
from ..core.tool import AnyAgentTool, ToolSpec, tool_spec
from ..mcp.client import McpClient, McpTurnSession
from ..mcp.directory import McpDirectory
from ..mcp.tools import McpToolOptions, build_mcp_tools, mcp_prompt_sections
from .builtin import RANDOM_NUMBER, ROLL_DICE, NotesTools

logger = logging.getLogger(__name__)


class SessionToolRegistry:
    """Набор инструментов хода — реализация зависимости ядра `ToolRegistry`."""

    def __init__(
        self, tools: Sequence[AnyAgentTool], instructions: Sequence[str], session: McpTurnSession
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._specs = [tool_spec(tool) for tool in tools]
        self._session = session
        self.instructions = tuple(instructions)
        """Секции системного промпта из текстовых инструкций подключённых серверов MCP."""

    @property
    def specs(self) -> Sequence[ToolSpec]:
        return self._specs

    @property
    def names(self) -> Sequence[str]:
        return list(self._tools)

    def find(self, name: str) -> AnyAgentTool | None:
        return self._tools.get(name)

    async def close(self) -> None:
        """Закрывает соединения, открытые за ход. Вызывается в задаче хода при любом исходе."""
        await self._session.close()


class ToolsFactory:
    def __init__(
        self, config: McpConfig, notes: NotesTools, directory: McpDirectory, client: McpClient
    ) -> None:
        self._config = config
        self._notes = notes
        self._directory = directory
        self._client = client

    async def for_session(
        self, session_id: str, allowed: Sequence[str] | None = None
    ) -> SessionToolRegistry:
        """Набор инструментов хода.

        `allowed` — имена инструментов, доступных сессии. Отсутствие означает весь набор.
        Перечень только сужает набор: предоставляет инструменты сервис, и вызывающая сторона
        может выбрать из имеющегося, но не добавить своего.
        """
        # Соединения хода открываются не здесь, а при первом вызове инструмента: набор
        # собирается из снимков, поэтому подготовка хода к серверам не обращается.
        session = self._client.open_session()
        try:
            connections = await self._directory.active()
        except Exception as error:
            # Отказ справочника не должен уносить ход: собственные инструменты остаются
            # работоспособны.
            logger.warning("подключения MCP не прочитаны: %s", error)
            connections = []

        external = build_mcp_tools(
            connections,
            session,
            McpToolOptions(
                call_timeout_s=self._config.call_timeout_s,
                on_diverged=self._on_diverged,
            ),
        )

        # Собственные инструменты идут первыми: при совпадении имён побеждает первая
        # регистрация, и уступать имя внешнему серверу сервис не должен.
        tools = self._deduplicate([*self._builtin(session_id), *external])
        if allowed is not None:
            permitted = set(allowed)
            tools = [tool for tool in tools if tool.name in permitted]

        self._warn_on_volume([tool_spec(tool) for tool in tools])
        return SessionToolRegistry(tools, mcp_prompt_sections(connections), session)

    def describe(self) -> list[ToolInfo]:
        """Имена и описания встроенных инструментов."""
        return [
            ToolInfo(name=tool.name, description=tool.description, source="builtin")
            for tool in self._builtin("preview")
        ]

    async def describe_all(self) -> list[ToolInfo]:
        """Полный состав: встроенные плюс инструменты подключений MCP."""
        connections = await self._directory.active()
        external = build_mcp_tools(
            connections,
            self._client.open_session(),
            McpToolOptions(call_timeout_s=self._config.call_timeout_s, on_diverged=_ignore),
        )
        return [
            *self.describe(),
            *(
                ToolInfo(name=tool.name, description=tool.description, source="mcp")
                for tool in external
            ),
        ]

    def _builtin(self, session_id: str) -> list[AnyAgentTool]:
        return [ROLL_DICE, RANDOM_NUMBER, *self._notes.for_session(session_id)]

    def _on_diverged(self, connection_id: str, message: str) -> None:
        # Повторное обнаружение запускается в фоне: ход, заметивший расхождение, его не ждёт.
        self._directory.schedule_stale(connection_id, message)

    @staticmethod
    def _deduplicate(tools: Sequence[AnyAgentTool]) -> list[AnyAgentTool]:
        """Отбрасывает инструменты с уже занятым именем.

        Совпадение возможно только со встроенным инструментом: префикс подключения уникален
        по справочнику, а имена внутри одного сервера уникальны по протоколу.
        """
        seen: set[str] = set()
        kept = []
        for tool in tools:
            if tool.name in seen:
                logger.error(
                    'имя инструмента "%s" уже занято; инструмент источника %s отброшен — '
                    "побеждает первая регистрация",
                    tool.name,
                    tool.source,
                )
                continue
            seen.add(tool.name)
            kept.append(tool)
        return kept

    def _warn_on_volume(self, specs: Sequence[ToolSpec]) -> None:
        """Предупреждение об объёме набора. Набор не сокращается: сокращение без ведома
        администратора спрятало бы причину, по которой агент не видит инструмента."""
        chars = sum(
            len(json.dumps(spec.parameters, ensure_ascii=False)) + len(spec.description)
            for spec in specs
        )
        if len(specs) <= self._config.warn_tool_count and chars <= self._config.warn_schema_chars:
            return
        logger.warning(
            "набор инструментов велик: %d инструментов, ~%d символов схем (пороги %d и %d)",
            len(specs),
            chars,
            self._config.warn_tool_count,
            self._config.warn_schema_chars,
        )


def _ignore(_connection_id: str, _message: str) -> None:
    pass
