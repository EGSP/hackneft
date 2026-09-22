"""Один ход агента.

Терминология. **Ход** — одно исполнение цикла от сообщения пользователя до итогового ответа.
**Шаг** — один виток внутри хода: обращение к модели плюс исполнение вызовов, которые оно
затребовало.

API модели не имеет памяти: каждое обращение отправляет весь массив сообщений заново. Модель
ничего не выполняет — она называет имя функции и аргументы, а выполняет сервис, кладёт
результат в массив и отправляет массив снова.

Прерывание отдельным исходом здесь не обрабатывается: отмена задачи asyncio доходит до цикла
исключением `CancelledError`, и различает её вызывающая сторона.
"""

import json
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from pydantic import JsonValue
from pydantic_core import to_jsonable_python

from hackneft_common.ai import (
    AssistantMessageEvent,
    AssistantNoteEvent,
    SessionEvent,
    ToolCallEvent,
    ToolOutcome,
    ToolResultEvent,
    TurnFinishedEvent,
)

from .budget import model_step
from .build_messages import build_messages
from .errors import StepLimitReached
from .messages import AgentMessage, AgentToolCall, AssistantMessage, ToolMessage, UserMessage
from .requirements import ObservedToolCall, TurnDeps
from .snapshot import request_snapshot, snapshot_prompt, snapshot_specs, sourced_tools
from .structured import final_answer, final_request
from .terminal import ASK_USER, CompletionMode, TurnFinish, is_terminal, terminal_text
from .tool import (
    AnyAgentTool,
    InputRejected,
    ToolFailure,
    ToolResult,
    describe_cause,
    format_tool_error,
)


@dataclass(frozen=True, slots=True)
class TurnOptions:
    provider: str
    """Провайдер модели — для отметки в журнале. Само обращение выполняет клиент модели."""
    model: str
    """Идентификатор модели — для отметки в журнале."""
    max_steps: int
    tool_result_max_chars: int
    system_prompt: str | None = None
    """Указания сервиса. Отсутствие означает промпт по умолчанию для дисциплины."""
    sections: tuple[str, ...] = ()
    """Секции, дописываемые после указаний: текстовые инструкции серверов MCP."""
    completion: CompletionMode = "chat"
    """Дисциплина завершения. В чат-сессии доступны оба терминальных инструмента, в
    агентской — только объявление итога: спрашивать некого."""
    result_schema: Mapping[str, Any] | None = None
    """JSON Schema итога. Если задана, итоговый ответ запрашивается объектом по ней (см.
    structured.py); иначе — обычным текстом."""


@dataclass(frozen=True, slots=True)
class TurnResult:
    text: str
    finish: TurnFinish
    """Чем закончился ход. Различие существенно для агентской сессии: итог она отдаёт
    вызывающей стороне, а вопрос задать некому."""
    steps: int
    tool_calls: int
    duration_ms: int
    data: JsonValue = None
    """Итог объектом по схеме итога. Пусто, если схема не задана."""


async def run_turn(
    history: Sequence[SessionEvent], options: TurnOptions, deps: TurnDeps
) -> TurnResult:
    """Исполняет ход по журналу сессии и возвращает его итог.

    Ход завершается, когда ответ модели не содержит требований вызова либо содержит
    терминальный вызов. Отказы хода объявляются исключениями `TurnError`.
    """
    started = time.monotonic()
    mode = options.completion

    # Запрос строится из снимка, а не рядом с ним: промпт и описания инструментов берутся из
    # того же объекта, что сохраняется. Набор в пределах хода не меняется, и снимок
    # сохраняется один раз на ход.
    snapshot = request_snapshot(
        completion=mode,
        prompt=options.system_prompt,
        sections=options.sections,
        tools=sourced_tools(deps.tools),
    )
    snapshot_id = await deps.snapshots.save(snapshot)
    specs = snapshot_specs(snapshot)
    messages = build_messages(snapshot_prompt(snapshot), history)
    tool_calls = 0

    async def finish(text: str, kind: TurnFinish, step: int, data: JsonValue = None) -> TurnResult:
        await deps.journal.append(AssistantMessageEvent(text=text))
        result = TurnResult(text, kind, step, tool_calls, _elapsed_ms(started), data)
        await deps.journal.append(
            TurnFinishedEvent(
                steps=result.steps, tool_calls=result.tool_calls, duration_ms=result.duration_ms
            )
        )
        return result

    async def conclude(conversation: list[AgentMessage], step: int) -> TurnResult:
        final = await final_answer(
            conversation,
            options.result_schema,
            first_step=step + 1,
            max_steps=options.max_steps,
            provider=options.provider,
            model=options.model,
            snapshot_id=snapshot_id,
            deps=deps,
        )
        return await finish(final.text, "completion", step + final.steps, final.data)

    for step in range(1, options.max_steps + 1):
        # Исчерпание бюджета вывода шаг обрабатывает сам повторами (budget.py) и отказом,
        # если повторы не помогли.
        answered = await model_step(
            messages,
            specs,
            step=step,
            max_steps=options.max_steps,
            provider=options.provider,
            model=options.model,
            snapshot_id=snapshot_id,
            deps=deps,
        )
        reply = answered.reply
        # Рассуждение оборванной попытки остаётся в контексте хода вместе с ответом на него.
        messages.extend(answered.context)

        text = reply.content.strip()

        if not reply.tool_calls:
            # Терминального вызова не было. Ход всё равно завершается: автопродолжение
            # требует записи подставного сообщения и решается отдельно. Итог по схеме
            # всё же запрашивается: вызывающей стороне нужен объект, а не текст.
            if options.result_schema is not None:
                return await conclude(
                    [
                        *messages,
                        AssistantMessage(text),
                        UserMessage(final_request(options.result_schema)),
                    ],
                    step,
                )
            return await finish(text, "plain", step)

        # Вызовы, стоящие в пачке после терминального, отбрасываются: модель уже объявила
        # работу законченной, и исполнять их значило бы действовать после выданного ответа.
        terminal_index = next(
            (i for i, call in enumerate(reply.tool_calls) if is_terminal(call.name, mode)), None
        )
        executable = (
            reply.tool_calls if terminal_index is None else reply.tool_calls[:terminal_index]
        )

        # Ответ модели добавляется в массив вместе с идентификаторами вызовов: сообщения с
        # ролью `tool` привязаны к ним, и без них следующий запрос будет отвергнут.
        if executable:
            messages.append(AssistantMessage(text, executable))

        # Текст, пришедший вместе с терминальным вызовом, не записывается: итог оформляет
        # терминальный вызов, и вторая запись стала бы вторым ответом на один ход.
        if text != "" and terminal_index is None:
            await deps.journal.append(AssistantNoteEvent(step=step, text=text))

        batch_size = len(executable)
        for batch_index, call in enumerate(executable, start=1):
            tool_calls += 1
            await deps.journal.append(
                ToolCallEvent(
                    call_id=call.id,
                    name=call.name,
                    raw_arguments=call.raw_arguments,
                    step=step,
                    batch_size=batch_size,
                    batch_index=batch_index,
                )
            )

            call_started = time.monotonic()
            # Наблюдение охватывает вызов целиком, включая сверку имени с набором и разбор
            # аргументов: отказ на этих действиях столь же значим, как отказ исполнения.
            result = await deps.observer.observe(
                ObservedToolCall(
                    call_id=call.id,
                    name=call.name,
                    raw_arguments=call.raw_arguments,
                    step=step,
                    batch_size=batch_size,
                    batch_index=batch_index,
                ),
                partial(
                    execute_call,
                    deps.tools.find(call.name),
                    deps.tools.names,
                    call,
                    options.tool_result_max_chars,
                ),
            )

            await deps.journal.append(
                ToolResultEvent(
                    call_id=call.id,
                    name=call.name,
                    kind=result.kind,
                    content=result.content,
                    duration_ms=_elapsed_ms(call_started),
                    step=step,
                    batch_size=batch_size,
                    batch_index=batch_index,
                )
            )
            messages.append(ToolMessage(call.id, result.content))

        if terminal_index is not None:
            call = reply.tool_calls[terminal_index]
            if call.name == ASK_USER:
                declared = terminal_text(call.raw_arguments)
                return await finish(declared if declared != "" else text, "question", step)
            # attempt_completion лишь объявляет конец работы; итог модель даёт ответом на
            # запрос, который возвращает этот вызов (structured.py).
            return await conclude(
                [
                    *messages,
                    AssistantMessage(text, (call,)),
                    ToolMessage(call.id, final_request(options.result_schema)),
                ],
                step,
            )

    raise StepLimitReached(options.max_steps)


DEFECT_HINT = (
    "Это внутренняя ошибка сервиса, а не ошибка вызова. Повтор того же вызова даст тот же "
    "результат: продолжай без этого инструмента либо сообщи, что задача невыполнима."
)
"""Подсказка модели при дефекте: повтор даст тот же результат, осмысленно лишь обойтись без
инструмента."""


def _failed(
    kind: ToolOutcome, message: str, hint: str | None, detail: str | None = None
) -> ToolResult:
    """Отказ вызова. Модели уходит `content`, наблюдателю — `detail`."""
    return ToolResult(kind, format_tool_error(message, hint), detail if detail else message)


async def execute_call(
    tool: AnyAgentTool | None, known_names: Sequence[str], call: AgentToolCall, max_chars: int
) -> ToolResult:
    """Исполняет один вызов.

    Отказ инструмента — штатный исход, а не ошибка хода: он возвращается модели результатом
    вызова, чтобы та исправилась на следующем шаге. Наверх пробрасывается только отмена.

    Усечение результата выполняется здесь же, а не у вызывающей стороны, чтобы журнал,
    сообщение модели и наблюдение содержали ровно один и тот же текст.
    """
    result = await _execute(tool, known_names, call)
    return replace(result, content=truncate(result.content, max_chars))


async def _execute(
    tool: AnyAgentTool | None, known_names: Sequence[str], call: AgentToolCall
) -> ToolResult:
    if tool is None:
        return _failed(
            "unknown_tool",
            f'Инструмента "{call.name}" не существует',
            f"Доступны: {', '.join(known_names)}. Вызови один из них.",
        )

    try:
        raw: object = {} if call.raw_arguments == "" else json.loads(call.raw_arguments)
    except json.JSONDecodeError as error:
        return _failed(
            "bad_arguments",
            f"Аргументы вызова {call.name} не разобраны: {describe_cause(error)}",
            "Повтори вызов, передав корректный объект JSON.",
        )

    checked = tool.input.check(raw)
    if isinstance(checked, InputRejected):
        return _failed(
            "schema_mismatch",
            f"Аргументы вызова {call.name} не соответствуют схеме: {checked.problems}",
            "Повтори вызов, исправив перечисленные поля.",
        )

    try:
        value = await tool.execute(checked.value)
        content = json.dumps(to_jsonable_python(value), ensure_ascii=False)
    except ToolFailure as failure:
        return _failed("tool_failure", failure.message, failure.hint)
    except Exception as defect:
        # Текст исключения модели не отдаётся: он адресован разработчику, раскрывает
        # устройство сервиса и расходует контекст, ничего не подсказывая. Модель получает
        # постоянную формулировку с предписанием, а полный текст уходит наблюдателю.
        return _failed(
            "defect",
            f"Инструмент {call.name} не выполнен: внутренняя ошибка сервиса.",
            DEFECT_HINT,
            "".join(traceback.format_exception(defect)).strip(),
        )
    return ToolResult("ok", content)


def truncate(content: str, max_chars: int) -> str:
    """Ограничение размера результата.

    Без него один объёмный вывод занимает окно контекста целиком, а поскольку каждый шаг
    отправляет всю историю заново, цена этого растёт с каждым шагом.
    """
    if len(content) <= max_chars:
        return content
    return (
        f"{content[:max_chars]}\n"
        f"… [результат усечён: показаны первые {max_chars} из {len(content)} символов]"
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
