"""Атрибуты спанов в двух наборах соглашений сразу.

`gen_ai.*` (OpenTelemetry) понимает Langfuse, `llm.*` и `openinference.*` (OpenInference) —
Phoenix. Наборы не конфликтуют, поэтому выставляются оба: это дешевле привязки к одному
приёмнику. OpenInference раскладывает диалог по сообщениям, и именно из этой раскладки
собирается вид переписки.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict

from opentelemetry.util.types import AttributeValue

from ..core.messages import (
    AgentMessage,
    AssistantMessage,
    ModelReply,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from ..core.requirements import ObservedToolCall
from ..core.tool import ToolResult

_VALUE_LIMIT = 4000
"""Предел длины значений входа и итога на спане: приёмник хранит атрибуты целиком."""


def _clip(text: str) -> str:
    if len(text) <= _VALUE_LIMIT:
        return text
    return f"{text[:_VALUE_LIMIT]}… [показаны первые {_VALUE_LIMIT} из {len(text)} символов]"


def session_attributes(session_id: str) -> dict[str, AttributeValue]:
    return {"session.id": session_id, "gen_ai.conversation.id": session_id}


def turn_input_attributes(text: str, capture: bool) -> dict[str, AttributeValue]:
    """Вход хода. Записывается при любой настройке: без него перечень сессий в приёмнике
    показывает пустую колонку первого входа. Различие лишь в том, попадает ли туда сам текст."""
    return {
        "input.value": _clip(text) if capture else f"запрос из {len(text)} символов",
        "input.mime_type": "text/plain",
    }


def turn_output_attributes(ok: bool, text: str, capture: bool) -> dict[str, AttributeValue]:
    if not ok:
        return {"output.value": _clip(f"Отказ: {text}"), "output.mime_type": "text/plain"}
    return {
        "output.value": _clip(text) if capture else f"ответ из {len(text)} символов",
        "output.mime_type": "text/plain",
    }


def chat_request_attributes(
    *,
    provider: str,
    model_name: str,
    model_uri: str,
    temperature: float,
    messages: Sequence[AgentMessage],
    tool_count: int,
    capture: bool,
) -> dict[str, AttributeValue]:
    attributes: dict[str, AttributeValue] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": provider,
        "gen_ai.request.model": model_uri,
        "gen_ai.request.temperature": temperature,
        "openinference.span.kind": "LLM",
        "llm.model_name": model_name,
        "llm.provider": provider,
        "llm.system": "openai",
        "llm.invocation_parameters": json.dumps(
            {"temperature": temperature, "tool_count": tool_count}
        ),
    }
    if not capture:
        return attributes

    attributes["input.value"] = json.dumps(
        {"messages": [{"role": _role(message), **asdict(message)} for message in messages]},
        ensure_ascii=False,
    )
    attributes["input.mime_type"] = "application/json"
    for index, message in enumerate(messages):
        prefix = f"llm.input_messages.{index}.message"
        attributes[f"{prefix}.role"] = _role(message)
        if isinstance(message, ToolMessage):
            attributes[f"{prefix}.content"] = message.content
            attributes[f"{prefix}.tool_call_id"] = message.call_id
            continue
        if message.content != "":
            attributes[f"{prefix}.content"] = message.content
        if isinstance(message, AssistantMessage):
            for call_index, call in enumerate(message.tool_calls):
                call_prefix = f"{prefix}.tool_calls.{call_index}.tool_call"
                attributes[f"{call_prefix}.id"] = call.id
                attributes[f"{call_prefix}.function.name"] = call.name
                attributes[f"{call_prefix}.function.arguments"] = call.raw_arguments
    return attributes


def chat_response_attributes(reply: ModelReply, capture: bool) -> dict[str, AttributeValue]:
    attributes: dict[str, AttributeValue] = {
        "gen_ai.usage.input_tokens": reply.usage.prompt,
        "gen_ai.usage.output_tokens": reply.usage.completion,
        "gen_ai.response.tool_calls": len(reply.tool_calls),
        "llm.token_count.prompt": reply.usage.prompt,
        "llm.token_count.completion": reply.usage.completion,
        "llm.token_count.total": reply.usage.prompt + reply.usage.completion,
    }
    if reply.finish_reason is not None:
        attributes["gen_ai.response.finish_reasons"] = [reply.finish_reason]
    if not capture:
        return attributes

    attributes["output.value"] = json.dumps(asdict(reply), ensure_ascii=False)
    attributes["output.mime_type"] = "application/json"
    attributes["llm.output_messages.0.message.role"] = "assistant"
    if reply.finish_reason is not None:
        attributes["llm.output_messages.0.message.finish_reason"] = reply.finish_reason
    if reply.reasoning is not None:
        # Сообщение с рассуждением раскладывается на части: часть с типом `reasoning`
        # приёмник показывает отдельным блоком. `message.content` при этом не выставляется —
        # иначе текст ответа отобразится дважды.
        parts = "llm.output_messages.0.message.contents"
        attributes[f"{parts}.0.message_content.type"] = "reasoning"
        attributes[f"{parts}.0.message_content.text"] = reply.reasoning
        if reply.content != "":
            attributes[f"{parts}.1.message_content.type"] = "text"
            attributes[f"{parts}.1.message_content.text"] = reply.content
        attributes["gen_ai.completion.reasoning"] = reply.reasoning[:16000]
    elif reply.content != "":
        attributes["llm.output_messages.0.message.content"] = reply.content
    for index, call in enumerate(reply.tool_calls):
        prefix = f"llm.output_messages.0.message.tool_calls.{index}.tool_call"
        attributes[f"{prefix}.id"] = call.id
        attributes[f"{prefix}.function.name"] = call.name
        attributes[f"{prefix}.function.arguments"] = call.raw_arguments
    return attributes


def tool_call_attributes(call: ObservedToolCall, capture: bool) -> dict[str, AttributeValue]:
    attributes: dict[str, AttributeValue] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": call.name,
        "gen_ai.tool.call.id": call.call_id,
        "openinference.span.kind": "TOOL",
        "tool.name": call.name,
        # Место вызова в ходе: отличает повтор того же вызова на следующем шаге от
        # одновременных вызовов в одной пачке.
        "hackneft.tool.step": call.step,
        "hackneft.tool.batch.size": call.batch_size,
        "hackneft.tool.batch.index": call.batch_index,
    }
    if capture:
        attributes["tool.parameters"] = call.raw_arguments
        attributes["input.value"] = call.raw_arguments
        attributes["input.mime_type"] = "application/json"
    return attributes


def tool_result_attributes(result: ToolResult, capture: bool) -> dict[str, AttributeValue]:
    """Класс исхода проставляется всегда, в том числе при успехе: по нему выбираются неверные
    вызовы. Содержимое пишется только при успехе: у отказа оно дублирует состояние спана."""
    attributes: dict[str, AttributeValue] = {"hackneft.tool.outcome": result.kind}
    if capture and result.kind == "ok":
        attributes["output.value"] = result.content[:8000]
        attributes["output.mime_type"] = "application/json"
    return attributes


def _role(message: AgentMessage) -> str:
    match message:
        case SystemMessage():
            return "system"
        case UserMessage():
            return "user"
        case AssistantMessage():
            return "assistant"
        case ToolMessage():
            return "tool"
