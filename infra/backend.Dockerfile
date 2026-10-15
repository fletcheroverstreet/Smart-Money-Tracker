# Backend image: API and worker share it (different commands).
# Python 3.14 on Debian trixie; every dependency ships cp314 wheels, so no compiler is needed.
FROM python:3.14-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app/backend
# Dependencies first (cached layer), then the source.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
COPY config/ /app/config/
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 smt && mkdir -p /data/raw && chown -R smt /data
USER smt

EXPOSE 8000
CMD ["smt", "api", "--host", "0.0.0.0", "--port", "8000"]
