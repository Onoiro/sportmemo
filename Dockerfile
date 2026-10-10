FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Системные библиотеки:
#   libpq5, postgresql-client — psycopg и pg_isready для healthcheck
#   libgl1, libglib2.0-0 — OpenCV (этап 3, но образ готовим сразу)
#   libjpeg62-turbo, zlib1g — Pillow
#   curl — healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        postgresql-client \
        libgl1 \
        libglib2.0-0 \
        libjpeg62-turbo \
        zlib1g \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Зависимости отдельным слоем: пересобираются только при изменении lock-файла
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

RUN adduser --disabled-password --gecos "" --uid 1000 appuser \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "config.wsgi:application"]
