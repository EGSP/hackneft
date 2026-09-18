"""Приведение результата вызова внешнего инструмента к виду, в каком результат возвращают
собственные инструменты.

После приведения цикл не различает происхождение инструмента ни при усечении, ни при записи в
журнал, ни при отказе.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from mcp_types import (
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)


@dataclass(frozen=True, slots=True)
class CallSucceeded:
    value: object


@dataclass(frozen=True, slots=True)
class CallFailed:
    message: str


McpCallOutcome = CallSucceeded | CallFailed


def normalize_call_result(result: CallToolResult) -> McpCallOutcome:
    """Приводит ответ `tools/call`.

    Порядок предпочтения содержателен. `structuredContent` есть заявленный сервером
    машиночитаемый результат, и если он есть, текстовые блоки его дублируют. Признак `isError`
    даёт отказ инструмента, а не успешный результат: он описывает неверный вызов, который
    модель может исправить.
    """
    text = _render_blocks(result.content)
    if result.is_error:
        return CallFailed(text or "Инструмент вернул ошибку без пояснения")
    if result.structured_content is not None:
        return CallSucceeded(result.structured_content)
    return CallSucceeded({"ok": True} if text == "" else _as_data(text))


def _as_data(text: str) -> object:
    """Разворачивает данные, присланные текстом.

    Значительная часть серверов возвращает JSON внутри текстового блока. Оставленный строкой,
    такой ответ кодируется в JSON второй раз, и модель получает содержимое с экранированными
    кавычками — читать его труднее, а расход токенов выше. Разворачиваются только объект и
    массив: они означают данные однозначно.
    """
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return text
    try:
        parsed: object = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    return parsed if isinstance(parsed, dict | list) else text


def _render_blocks(blocks: Sequence[object]) -> str:
    """Сводит блоки содержимого в текст.

    Изображения и двоичные ресурсы заменяются описанием типа и объёма: проведение их через
    контекст модели означает расход токенов без пользы. Заменяющая строка сообщает модели, что
    данные существуют, — иначе она сочтёт вызов безрезультатным и повторит его.
    """
    parts: list[str] = []
    for block in blocks:
        match block:
            case TextContent():
                parts.append(block.text)
            case ImageContent() | AudioContent():
                kind = "изображение" if isinstance(block, ImageContent) else "звук"
                size = len(block.data) * 3 // 4
                parts.append(
                    f"[{kind} {block.mime_type}, ~{size} байт: содержимое в ответ не включено, "
                    "сервис не передаёт двоичные данные модели]"
                )
            case EmbeddedResource():
                resource = block.resource
                if isinstance(resource, TextResourceContents):
                    parts.append(resource.text)
                else:
                    parts.append(f"[ресурс {resource.uri}: содержимое в ответ не включено]")
            case ResourceLink():
                parts.append(f"[ресурс {block.uri}: содержимое в ответ не включено]")
            case _:
                kind_name = getattr(block, "type", type(block).__name__)
                parts.append(f'[блок типа "{kind_name}" сервисом не отображается]')
    return "\n".join(parts).strip()
