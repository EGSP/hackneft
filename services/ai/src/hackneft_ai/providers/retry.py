"""Повтор при сетевых отказах.

Повторяются только отказы соединения: ответ с кодом состояния повтором не исправляется, а
неверный запрос останется неверным. Истечение времени чтения тоже не повторяется: обращение к
модели к этому моменту могло уже израсходовать токены. Задержка растёт экспоненциально и
размывается случайной добавкой, чтобы одновременно отказавшие обращения не повторились
синхронно.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx2
import openai

_RETRYABLE_HTTP = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.ReadError,
    httpx2.WriteError,
    httpx2.RemoteProtocolError,
)


def is_retryable(error: BaseException) -> bool:
    if isinstance(error, openai.APITimeoutError):
        return False
    if isinstance(error, openai.APIConnectionError):
        return True
    return isinstance(error, _RETRYABLE_HTTP)


async def with_retry[T](operation: Callable[[], Awaitable[T]], *, attempts: int = 3) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as error:
            if attempt == attempts or not is_retryable(error):
                raise
            delay = 0.4 * 2 ** (attempt - 1)
            await asyncio.sleep(delay + random.random() * delay)
    raise AssertionError("unreachable")


def describe_error_chain(error: BaseException) -> str:
    """Текст отказа вместе с вложенной причиной: «Connection error. (ConnectError: …)»."""
    text = str(error) or type(error).__name__
    inner = error.__cause__ or error.__context__
    if inner is None or inner is error:
        return text
    return f"{text} ({type(inner).__name__}: {str(inner) or '—'})"
