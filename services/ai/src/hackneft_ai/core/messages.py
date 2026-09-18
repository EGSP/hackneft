"""Сообщения диалога в собственных типах ядра.

Типы SDK провайдера сюда не проникают намеренно: цикл не должен меняться при добавлении
провайдера. Преобразование в формат конкретного API выполняет адаптер провайдера на границе.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    """Вызов инструмента, затребованный моделью. Аргументы приходят строкой JSON."""

    id: str
    name: str
    raw_arguments: str


@dataclass(frozen=True, slots=True)
class SystemMessage:
    content: str


@dataclass(frozen=True, slots=True)
class UserMessage:
    content: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    content: str
    tool_calls: tuple[AgentToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolMessage:
    call_id: str
    content: str


AgentMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Расход токенов на одно обращение к модели."""

    prompt: int
    completion: int


@dataclass(frozen=True, slots=True)
class ModelReply:
    """Ответ модели на одно обращение."""

    content: str
    tool_calls: tuple[AgentToolCall, ...]
    reasoning: str | None
    """Текст рассуждения. Рассуждающие модели тратят на него часть выходного бюджета и
    возвращают отдельным полем; в историю диалога он не возвращается."""
    finish_reason: str | None
    """`length` означает, что модель упёрлась в предел выходных токенов."""
    usage: TokenUsage
