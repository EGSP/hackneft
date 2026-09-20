# syntax=docker/dockerfile:1
#
# Образ агрегатора. Собирается из корня репозитория: сервису нужны общие uv.lock и libs/common.
#
#   docker build -f services/aggregator/Dockerfile -t hackneft-aggregator .
#   docker run -p 8200:8200 -v $PWD/../data:/data/source:ro hackneft-aggregator
#
# Файлы телеметрии в образ не входят: пакет данных лежит вне репозитория и занимает сотни
# мегабайт. Каталог с ними пробрасывается томом только для чтения; путь задаёт
# AGGREGATOR_SOURCE_DIR. Перечень настроек — в services/aggregator/.env.example.

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
    uv sync --frozen --no-dev --package hackneft-aggregator --no-install-workspace

COPY libs/common/src libs/common/src
COPY services/aggregator/src services/aggregator/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package hackneft-aggregator


FROM python:3.12-slim

# Пакеты сервиса установлены в режиме редактируемой установки, поэтому исходники переносятся
# вместе с окружением и по тем же путям: статика интерфейса ищется рядом с app.py.
COPY --from=build /app /app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin hackneft \
    && mkdir -p /data/state /data/source \
    && chown hackneft /data/state

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AGGREGATOR_HOST=0.0.0.0 \
    AGGREGATOR_PORT=8200 \
    AGGREGATOR_SOURCE_DIR=/data/source \
    AGGREGATOR_STATE_PATH=/data/state/cursor.json

USER hackneft
WORKDIR /app/services/aggregator
VOLUME /data/state
EXPOSE 8200

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['AGGREGATOR_PORT']}/health\", timeout=4)"]

CMD ["hackneft-aggregator"]
