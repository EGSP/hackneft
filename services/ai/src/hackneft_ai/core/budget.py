"""Шаг обращения к модели с ответом без рассуждения после оборванного рассуждения.

Рассуждающая модель иногда зацикливается: приходит к выводу, а затем раз за разом
перепроверяет одно и то же, пока не израсходует весь бюджет вывода, так и не дав ответа. По
журналам сервиса цикл начинается на первых 10–20 % оборванного рассуждения, то есть вывод к
этому моменту уже сделан.

Поэтому оборванный шаг не повторяется с начала. Сервис обращается к модели ещё раз с
выключенным рассуждением (`reasoning_effort = none`) и передаёт ей её же рассуждение до начала
повторов с указанием сразу дать ответ шага. Если модель упёрлась в предел и так, ход
завершается отказом. Ответ, данный после рассуждения в пределах бюджета, используется как
есть: лишнего рассуждения в нём не было.

Рассуждение оборванной попытки вместе с указанием остаётся в контексте хода и после повтора:
ответ шага опирается на него, и следующим шагам оно нужно, чтобы ответ был понятен. В журнал
оно сообщением не пишется — оно уже записано рассуждением ответа модели, — а повод повтора
виден по отметке `retry` события начала шага.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hackneft_common.ai import BudgetRetry, ModelReplyEvent, StepStartedEvent

from .errors import OutputBudgetExhausted
from .messages import AgentMessage, ModelReply, UserMessage
from .requirements import TurnDeps
from .tool import ToolSpec

_RETRIES: tuple[BudgetRetry | None, ...] = (None, "no_reasoning")

REASONING_MAX_CHARS = 20_000
"""Предел длины рассуждения, передаваемого в повтор, если начало повторов не найдено."""

_MIN_REPEATED_LINE = 16
"""Короче этого строка не считается признаком цикла: «Okay.» и «Let's go.» повторяются и в
нормальном рассуждении."""


@dataclass(frozen=True, slots=True)
class StepReply:
    reply: ModelReply
    context: tuple[AgentMessage, ...] = ()
    """Сообщения, которые вызывающая сторона добавляет в контекст хода перед ответом: указание
    повтора с рассуждением оборванной попытки. Пусто, если повтора не было."""


def exhausted(reply: ModelReply, strict: bool) -> bool:
    """Ответ оборван пределом вывода и пользы не несёт.

    Без вызовов инструментов и с пустым текстом — всегда. При ответе по схеме — и с частичным
    текстом: оборванный объект JSON всё равно не разобрать.
    """
    if reply.finish_reason != "length" or reply.tool_calls:
        return False
    return strict or reply.content.strip() == ""


def before_loop(reasoning: str) -> str:
    """Рассуждение до начала цикла.

    Началом цикла считается второе появление строки, которая встречается трижды: с этого места
    модель повторяет уже сказанное.
    """
    counts: dict[str, int] = {}
    second: dict[str, int] = {}
    position = 0
    for line in reasoning.splitlines(keepends=True):
        key = line.strip()
        if len(key) >= _MIN_REPEATED_LINE:
            counts[key] = counts.get(key, 0) + 1
            if counts[key] == 2:
                second[key] = position
            elif counts[key] == 3:
                return reasoning[: second[key]].rstrip()
        position += len(line)
    return reasoning[:REASONING_MAX_CHARS].rstrip()


def answer_request(reasoning: str | None, strict: bool) -> str:
    """Указание повтора: вывод уже сделан, нужен только ответ шага."""
    kind = "объект JSON по схеме" if strict else "вызов инструмента или текст"
    head = "Твоё рассуждение над этим шагом прервано пределом вывода, ответа не получено."
    body = (
        " Вот оно до начала повторов:\n\n"
        f"<рассуждение>\n{before_loop(reasoning)}\n</рассуждение>\n\n"
        "Выводы в нём уже сделаны."
        if reasoning
        else ""
    )
    return f"{head}{body} Не рассуждай заново: сразу дай ответ этого шага — {kind}."


async def model_step(
    messages: Sequence[AgentMessage],
    specs: Sequence[ToolSpec],
    *,
    step: int,
    max_steps: int,
    provider: str,
    model: str,
    snapshot_id: str,
    deps: TurnDeps,
    result_schema: Mapping[str, Any] | None = None,
    reasoning: bool = True,
) -> StepReply:
    """Обращение шага к модели. Каждая попытка записывается в журнал началом шага и ответом.

    `reasoning=False` выключает рассуждение с первой попытки; повторять такой шаг без
    рассуждения бессмысленно, поэтому попытка одна.
    """
    strict = result_schema is not None
    reply: ModelReply | None = None
    context: tuple[AgentMessage, ...] = ()
    for retry in _RETRIES if reasoning else (None,):
        # Записывается до обращения к модели: иначе журнал молчит всё время, пока модель
        # формирует ответ, а это почти вся длительность хода.
        await deps.journal.append(
            StepStartedEvent(
                step=step,
                max_steps=max_steps,
                provider=provider,
                model=model,
                snapshot_id=snapshot_id,
                retry=retry,
            )
        )
        if reply is not None:
            context = (UserMessage(answer_request(reply.reasoning, strict)),)
        reply = await deps.model.complete(
            [*messages, *context],
            specs,
            result_schema=result_schema,
            reasoning_effort="none" if retry == "no_reasoning" or not reasoning else None,
        )
        # Ответ записывается до разбора его содержимого: расход токенов нужен и тогда, когда
        # ход на этом ответе оборвётся.
        await deps.journal.append(
            ModelReplyEvent(
                step=step,
                prompt_tokens=reply.usage.prompt,
                completion_tokens=reply.usage.completion,
                finish_reason=reply.finish_reason,
                reasoning=reply.reasoning,
            )
        )
        if not exhausted(reply, strict):
            return StepReply(reply, context)
    assert reply is not None
    raise OutputBudgetExhausted(reply.usage.completion, reply.reasoning is not None)
