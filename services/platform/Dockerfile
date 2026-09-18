# syntax=docker/dockerfile:1
#
# Образ сервиса платформы. Собирается из корня репозитория: сервису нужны общие uv.lock и
# libs/common.
#
#   docker build -f services/platform/Dockerfile -t hackneft-platform .
#   docker run -p 8000:8000 -v hackneft-platform-data:/data hackneft-platform
#
# Настройки передаются переменными окружения, перечень — в services/platform/.env.example.

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Зависимости ставятся отдельным слоем до копирования исходников: пока не менялись uv.lock и
# pyproject.toml, правка кода пересобирает только последний слой. Описания всех участников
# workspace нужны uv для разбора lock-файла, хотя ставятся зависимости одного сервиса.
COPY pyproject.toml uv.lock ./
COPY libs/common/pyproject.toml libs/common/
COPY services/ai/pyproject.toml services/ai/
COPY services/platform/pyproject.toml services/platform/
COPY services/aggregator/pyproject.toml services/aggregator/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package hackneft-platform --no-install-workspace

COPY libs/common/src libs/common/src
COPY services/platform/src services/platform/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package hackneft-platform


FROM python:3.12-slim

# Пакеты сервиса установлены в режиме редактируемой установки, поэтому исходники переносятся
# вместе с окружением и по тем же путям: статика интерфейса ищется рядом с api.py.
COPY --from=build /app /app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin hackneft \
    && mkdir /data \
    && chown hackneft /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PLATFORM_PORT=8000 \
    PLATFORM_DATABASE_PATH=/data/platform.db

USER hackneft
WORKDIR /app/services/platform
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PLATFORM_PORT']}/health\", timeout=4)"]

CMD ["sh", "-c", "exec uvicorn hackneft_platform.api:app --host 0.0.0.0 --port \"$PLATFORM_PORT\""]
