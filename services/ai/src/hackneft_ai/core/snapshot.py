"""Снимок постоянной части запроса к модели: указаний сервиса, секций и описаний инструментов.

Цикл строит запрос из снимка, а не рядом с ним: промпт и перечень инструментов для обращения
к модели берутся из того же объекта, который сохраняется. Поэтому снимок, на который
ссылается журнал, совпадает с отправленным по построению, а не по соглашению.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from hackneft_common.ai import RequestSnapshotContent, SnapshotTool, ToolSource

from .requirements import ToolRegistry
from .system_prompt import system_prompt, with_sections
from .terminal import CompletionMode, terminal_specs
from .tool import ToolSpec


@dataclass(frozen=True, slots=True)
class SourcedTool:
    """Инструмент набора вместе с происхождением."""

    spec: ToolSpec
    source: ToolSource


def request_snapshot(
    *,
    completion: CompletionMode,
    tools: Sequence[SourcedTool],
    sections: Sequence[str] = (),
    prompt: str | None = None,
) -> RequestSnapshotContent:
    """Собирает снимок. Отсутствие `prompt` означает промпт по умолчанию для дисциплины.

    Терминальные инструменты добавляются к набору здесь, а не берутся из реестра: реестр
    предоставляет действия, а они — способ объявить, чем ход закончен.
    """
    terminal = [SourcedTool(spec, "builtin") for spec in terminal_specs(completion)]
    return RequestSnapshotContent(
        prompt=prompt if prompt is not None else system_prompt(completion),
        # Пустая секция к промпту не дописывается, поэтому и в снимок она не попадает.
        sections=[section for section in sections if section.strip() != ""],
        tools=[
            SnapshotTool(
                name=tool.spec.name,
                description=tool.spec.description,
                parameters=tool.spec.parameters,
                source=tool.source,
            )
            for tool in (*tools, *terminal)
        ],
    )


def sourced_tools(registry: ToolRegistry) -> list[SourcedTool]:
    """Набор реестра с происхождением инструментов."""
    result = []
    for spec in registry.specs:
        tool = registry.find(spec.name)
        result.append(SourcedTool(spec, tool.source if tool is not None else "builtin"))
    return result


def snapshot_prompt(snapshot: RequestSnapshotContent) -> str:
    """Системный промпт в том виде, в каком он уходит модели."""
    return with_sections(snapshot.prompt, snapshot.sections)


def snapshot_specs(snapshot: RequestSnapshotContent) -> list[ToolSpec]:
    """Описания инструментов в том виде, в каком они уходят модели."""
    return [
        ToolSpec(name=tool.name, description=tool.description, parameters=dict(tool.parameters))
        for tool in snapshot.tools
    ]
