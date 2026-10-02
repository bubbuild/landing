FROM ghcr.io/astral-sh/uv:0.10.12 AS uv
FROM docker.io/litestream/litestream:0.5.17 AS litestream
FROM python:3.12-slim-bookworm AS builder
ARG TARGETARCH
ADD https://github.com/cli/cli/releases/download/v2.97.0/gh_2.97.0_linux_${TARGETARCH}.tar.gz /tmp/gh.tar.gz
RUN tar -xzf /tmp/gh.tar.gz --strip-components=2 -C /usr/local/bin gh_2.97.0_linux_${TARGETARCH}/bin/gh

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /opt/landing
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-cache

FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/PsiACE/landing"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 landing \
    && useradd --uid 1000 --gid 1000 --create-home landing \
    && mkdir -p /storage/workspace /replica /run/landing \
    && chown -R 1000:1000 /storage /replica /run/landing \
    && chmod 700 /run/landing
COPY --from=builder /opt/landing/.venv /opt/landing/.venv
COPY --from=builder /usr/local/bin/gh /usr/local/bin/gh
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
COPY container/litestream.yml /etc/litestream.yml
COPY --chmod=755 container/entrypoint /usr/local/bin/landing-entrypoint

ENV PATH="/opt/landing/.venv/bin:$PATH" \
    LANDING_DB="/storage/landing.sqlite3" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
WORKDIR /storage
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/up', timeout=3).close()"
ENTRYPOINT ["landing-entrypoint"]
CMD ["serve", "--host", "0.0.0.0", "--port", "80"]
