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
# Resolve live from pyprojects (no lock file). CPU torch index for [ml].
RUN if [ "$PLATFORM_DEV" = "1" ]; then \
      pip install --upgrade --extra-index-url https://download.pytorch.org/whl/cpu \
        "./backend[dev]" "./workers[ml,dev]"; \
    else \
      pip install --upgrade --extra-index-url https://download.pytorch.org/whl/cpu \
        ./backend "./workers[ml]"; \
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
# Code shared by both deployables. Both pyproject.toml files put "../shared" on
# pytest's pythonpath, so this must land as a sibling of backend/ and workers/
# (prod gets the same package via deploy/scripts/link_shared.sh instead).
COPY shared shared
COPY deploy deploy
# Same mechanism prod uses (a .pth in site-packages, see the script) instead
# of a second, Docker-only way to make algorand_shared importable at runtime.
# pytest's own resolution (pythonpath = ["../shared"] in both pyproject.toml)
# is unaffected and unrelated -- this is only for gunicorn/celery at runtime.
RUN deploy/scripts/link_shared.sh /usr/local shared
COPY schema schema
COPY conduit/schema conduit/schema

COPY docker/bin /usr/local/bin/platform-docker/
RUN chmod +x /usr/local/bin/platform-docker/*.sh
