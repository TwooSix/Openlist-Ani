# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.11-alpine AS builder

ENV UV_LINK_MODE=copy \
    PATH="/.venv/bin:$PATH"

WORKDIR /

RUN apk add --no-cache --virtual .build-deps \
        cargo \
        gcc \
        linux-headers \
        make \
        musl-dev \
        rust

COPY pyproject.toml uv.lock README.md ./

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project

COPY src/ /src
COPY docker/entrypoint.sh /entrypoint.sh

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable && \
    chmod +x /entrypoint.sh

FROM ghcr.io/astral-sh/uv:python3.11-alpine AS runtime

ENV PATH="/.venv/bin:$PATH"

WORKDIR /

COPY --from=builder /.venv /.venv
COPY --from=builder /entrypoint.sh /entrypoint.sh

ENV CONFIG_PATH=/config.toml
ENV ENABLE_ASSISTANT=false

CMD ["/entrypoint.sh"]
