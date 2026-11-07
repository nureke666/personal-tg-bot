FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY bot ./bot

RUN useradd --create-home --uid 1000 bot && mkdir -p /app/data && chown bot:bot /app/data
USER bot

CMD ["/app/.venv/bin/python", "-m", "bot"]
