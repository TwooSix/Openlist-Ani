# syntax=docker/dockerfile:1.7
FROM python:3.14-alpine AS builder

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/openlist-ani/.venv \
    PATH="/opt/openlist-ani/.venv/bin:$PATH"

WORKDIR /opt/openlist-ani

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    apk add --no-cache --virtual .build-deps \
        cargo \
        gcc \
        linux-headers \
        make \
        musl-dev \
        rust && \
    uv sync --frozen --no-dev --no-install-project && \
    apk del .build-deps

COPY src/ ./src
COPY docker/entrypoint.sh /entrypoint.sh
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable && \
    chmod +x /entrypoint.sh

FROM python:3.14-alpine AS runtime

ENV PATH="/opt/openlist-ani/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder --chmod=0555 /opt/openlist-ani/.venv /opt/openlist-ani/.venv
COPY --from=builder --chmod=0555 /entrypoint.sh /entrypoint.sh

RUN addgroup -S -g 1000 openlistani && \
    adduser -S -D -H -u 1000 -G openlistani openlistani && \
    mkdir -p /data /logs /skills && \
    touch /config.toml && \
    chown openlistani:openlistani /config.toml /data /logs /skills

ENV CONFIG_PATH=/config.toml
ENV ENABLE_ASSISTANT=false

USER openlistani
WORKDIR /

CMD ["/entrypoint.sh"]
