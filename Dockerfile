FROM ghcr.io/astral-sh/uv:0.10.12 AS uv
FROM docker.io/litestream/litestream:0.5.17 AS litestream
FROM python:3.12-slim-bookworm AS builder

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /opt/landing
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-cache

FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/bubbuild/landing"

ADD --chmod=644 https://cli.github.com/packages/githubcli-archive-keyring.gpg /etc/apt/keyrings/githubcli-archive-keyring.gpg
RUN printf '%s\n' 'deb [signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main' > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends git gh ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 landing \
    && useradd --uid 1000 --gid 1000 --create-home landing \
    && mkdir -p /storage/workspace /replica /run/landing \
    && chown -R 1000:1000 /storage /replica /run/landing \
    && chmod 700 /run/landing
COPY --from=builder /opt/landing/.venv /opt/landing/.venv
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
COPY container/litestream.yml /etc/litestream.yml
COPY --chmod=755 container/entrypoint /usr/local/bin/landing-entrypoint

ENV PATH="/opt/landing/.venv/bin:$PATH" \
    LANDING_DB="/storage/landing.sqlite3" \
    LANDING_REPLICATE="true" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
WORKDIR /storage
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/up', timeout=3).close()"
ENTRYPOINT ["landing-entrypoint"]
CMD ["serve", "--host", "0.0.0.0", "--port", "80"]
