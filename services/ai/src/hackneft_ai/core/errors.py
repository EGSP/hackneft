"""Отказы хода, которые цикл объявляет сам."""


class TurnError(Exception):
    """Отказ хода, известный циклу. Всё прочее считается дефектом сервиса."""


class ModelFailure(TurnError):
    """Отказ на стороне провайдера модели. Повтор осмыслен, исправление — нет."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StepLimitReached(TurnError):
    """Сработало ограничение числа шагов.

    Означает, что задача слишком велика для одного хода либо модель зациклилась на одном
    инструменте.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(f"step limit {limit}")
        self.limit = limit


class OutputBudgetExhausted(TurnError):
    """Модель израсходовала выходной бюджет, не сформировав ответ.

    У рассуждающих моделей это типичный исход: размышление занимает выход целиком. Чинится
    настройками, а не повтором.
    """

    def __init__(self, completion_tokens: int, had_reasoning: bool) -> None:
        super().__init__(f"output budget {completion_tokens}")
        self.completion_tokens = completion_tokens
        self.had_reasoning = had_reasoning


def describe_turn_error(error: TurnError) -> str:
    """Читаемое сообщение об исходе для журнала и интерфейса."""
    if isinstance(error, ModelFailure):
        return error.message
    if isinstance(error, StepLimitReached):
        return (
            f"Ход остановлен: превышен предел в {error.limit} шагов. Задача, вероятно, "
            "слишком велика для одного хода, либо модель зациклилась на одном инструменте."
        )
    if isinstance(error, OutputBudgetExhausted):
        cause = (
            "не сформировав ответ: весь выход занял текст рассуждения."
            if error.had_reasoning
            else "не сформировав ответ."
        )
        return (
            f"Модель исчерпала бюджет выходных токенов ({error.completion_tokens}), {cause} "
            "Увеличьте AGENT_MAX_TOKENS, упростите запрос либо возьмите модель без режима "
            "рассуждения."
        )
    return str(error)
