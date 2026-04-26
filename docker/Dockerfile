FROM ghcr.io/astral-sh/uv:python3.11-alpine

COPY src/ /src
COPY pyproject.toml /pyproject.toml
COPY uv.lock /uv.lock
COPY README.md /README.md
COPY docker/entrypoint.sh /entrypoint.sh

WORKDIR /

RUN apk add --no-cache gcc musl-dev linux-headers rust cargo make && \
    uv sync --frozen --no-cache && \
    apk del gcc musl-dev linux-headers rust cargo make && \
    uv cache clean && \
    rm -rf \
        /var/cache/apk/* \
        /root/.cache \
        /tmp/* && \
    chmod +x /entrypoint.sh

ENV CONFIG_PATH=/config.toml
ENV ENABLE_ASSISTANT=false

CMD ["/entrypoint.sh"]
