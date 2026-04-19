FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

RUN groupadd -r bot && useradd -r -g bot bot \
    && mkdir -p /app/logs /app/state \
    && chown -R bot:bot /app
USER bot

ENV CONFIG_PATH=/app/config/accounts.yaml \
    PYTHONPATH=/app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "src.main"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys; sys.exit(0 if os.path.exists(os.environ.get('CONFIG_PATH','/app/config/accounts.yaml')) else 1)"
