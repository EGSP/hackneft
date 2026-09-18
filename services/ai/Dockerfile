# syntax=docker/dockerfile:1
#
# Образ ИИ-сервиса. Собирается из корня репозитория: сервису нужны общие uv.lock и libs/common.
#
#   docker build -f services/ai/Dockerfile -t hackneft-ai .
#   docker run -p 8100:8100 -v hackneft-ai-data:/data --env-file services/ai/.env hackneft-ai
#
# Настройки передаются переменными окружения, перечень — в services/ai/.env.example.

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
    uv sync --frozen --no-dev --package hackneft-ai --no-install-workspace

COPY libs/common/src libs/common/src
COPY services/ai/src services/ai/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package hackneft-ai


FROM python:3.12-slim

# Пакеты сервиса установлены в режиме редактируемой установки, поэтому исходники переносятся
# вместе с окружением и по тем же путям: от расположения config.py отсчитывается каталог
# сервиса.
COPY --from=build /app /app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin hackneft \
    && mkdir /data \
    && chown hackneft /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AI_HOST=0.0.0.0 \
    AI_PORT=8100 \
    AI_DATABASE_PATH=/data/ai.db

USER hackneft
WORKDIR /app/services/ai
VOLUME /data
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['AI_PORT']}/health\", timeout=4)"]

CMD ["hackneft-ai"]
