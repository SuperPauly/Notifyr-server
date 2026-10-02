FROM ghcr.io/astral-sh/uv:0.9.28 AS uv
FROM ubuntu:24.04 AS build
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv python install 3.13 && uv sync --frozen --no-dev --no-editable --compile-bytecode

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 notifyr \
    && useradd --uid 10001 --gid 10001 --no-create-home notifyr \
    && mkdir /data && chown 10001:10001 /data
COPY --from=build /opt/python /opt/python
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH NOTIFYR_DB=/data/notifyr.sqlite PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3)"]
CMD ["uvicorn", "notifyr.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--proxy-headers"]
