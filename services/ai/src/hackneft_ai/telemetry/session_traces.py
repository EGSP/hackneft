"""Контексты трассировки работающих сессий.

Дерево спанов строится из дерева сессий: сервис знает, какая сессия какую породила, поэтому
достаточно помнить контекст каждой работающей сессии, и дочерняя привязывается к
родительской. Запись живёт ровно столько, сколько идёт ход: между ходами активного спана нет.
"""

import re

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, TraceFlags

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


class SessionTraceRegistry:
    def __init__(self) -> None:
        self._contexts: dict[str, Context] = {}

    def open(self, session_id: str, span: Span, parent: Context | None = None) -> Context:
        """Запоминает контекст на время хода."""
        context = trace.set_span_in_context(span, parent or Context())
        self._contexts[session_id] = context
        return context

    def close(self, session_id: str) -> None:
        self._contexts.pop(session_id, None)

    def parent_for(self, parent_id: str | None, traceparent: str | None) -> Context:
        """Контекст родителя с учётом явно переданного `traceparent`.

        Переданное значение имеет приоритет над деревом сессий: оно точнее и привязывает
        потомка к тому месту работы, где он действительно создан. Пустой контекст означает,
        что родителя нет либо его ход уже завершён, — и то и другое даёт корневой спан.
        """
        explicit = _context_from_traceparent(traceparent)
        if explicit is not None:
            return explicit
        if parent_id is None:
            return Context()
        return self._contexts.get(parent_id, Context())


def _context_from_traceparent(traceparent: str | None) -> Context | None:
    """Разбор заголовка W3C Trace Context. Неразобранное значение равносильно отсутствующему."""
    if traceparent is None:
        return None
    parts = _TRACEPARENT.match(traceparent.strip())
    if parts is None:
        return None
    span_context = SpanContext(
        trace_id=int(parts[1], 16),
        span_id=int(parts[2], 16),
        is_remote=True,
        trace_flags=TraceFlags(int(parts[3], 16) & TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context), Context())
