# Official Python 3.12 slim-bookworm image, pinned through the local mirror.
ARG PYTHON_IMAGE=mirror.gcr.io/library/python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_IMAGE} AS builder

ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=/tmp/uv-cache
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.5
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/tmp/uv-cache uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/tmp/uv-cache uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS web
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TZ=Asia/Shanghai
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY indices.toml alembic.ini ./
COPY migrations ./migrations
RUN python -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); ZoneInfo('America/New_York')"
USER 10001:10001
EXPOSE 8000
CMD ["index-valuation", "serve", "--host", "0.0.0.0", "--port", "8000"]
