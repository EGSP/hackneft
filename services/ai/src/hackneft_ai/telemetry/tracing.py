"""Трассировка через OpenTelemetry.

Экспорт выключен, пока не задан адрес приёмника: при пустом адресе провайдер трассировки не
регистрируется, и вызовы API OpenTelemetry из остального кода становятся пустыми операциями.
Дерево спанов повторяет устройство цикла: `invoke_agent`, внутри — `chat` и `execute_tool`.
"""

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ..config import TracingConfig

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def init_tracing(config: TracingConfig) -> None:
    global _provider
    if not config.enabled or _provider is not None:
        return
    _provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: config.service_name, SERVICE_VERSION: "0.1.0"})
    )
    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{config.endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(_provider)


def shutdown_tracing() -> None:
    global _provider
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception as error:
        # Недоступный приёмник не должен мешать остановке сервиса.
        logger.warning("остановка трассировки: %s", error)
    _provider = None


def tracer() -> trace.Tracer:
    return trace.get_tracer("hackneft-ai")
