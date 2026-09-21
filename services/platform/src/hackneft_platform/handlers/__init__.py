# Импорт подмодулей регистрирует их обработчики в диспетчере событий
# (см. hackneft_platform.events.dispatcher) как побочный эффект импорта.
from hackneft_platform.handlers import ai_notify, sulfur_stream

__all__ = ["ai_notify", "sulfur_stream"]
