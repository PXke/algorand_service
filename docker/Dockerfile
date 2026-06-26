# TESTING ONLY — not used for production deploy (see deploy/ + systemd).
#
# Base image pinned to match docs/CI (python:3.14-slim-bookworm).
# Build from repo root:
#   docker build -f docker/Dockerfile -t algorand-platform-test:local .
#
# PLATFORM_DEV=1 (default): pytest, ruff, vulture in image.
# PLATFORM_DEV=0: runtime deps only (smaller image for prod-like smoke).

FROM python:3.14-slim-bookworm

ARG PLATFORM_DEV=1
ARG PLATFORM_BROWSER=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY backend/pyproject.toml backend/
COPY workers/pyproject.toml workers/
RUN if [ "$PLATFORM_DEV" = "1" ]; then \
      pip install "./backend[dev]" ./workers; \
    else \
      pip install ./backend ./workers; \
    fi

# Playwright Chromium for worker browser scraper (hard targets / Discord web).
RUN if [ "$PLATFORM_BROWSER" = "1" ]; then \
      apt-get update \
      && playwright install-deps chromium \
      && playwright install chromium \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY backend/app backend/app
COPY backend/schema backend/schema
COPY backend/tests backend/tests
COPY workers/app workers/app
COPY workers/tests workers/tests
COPY deploy deploy
COPY schema schema
COPY conduit/schema conduit/schema

COPY docker/bin /usr/local/bin/platform-docker/
RUN chmod +x /usr/local/bin/platform-docker/*.sh
