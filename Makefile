# Local testing only — not production deploy (see deploy/README.md).

export BUILDX_BUILDER ?= default
export DOCKER_BUILDKIT ?= 1
export COMPOSE_DOCKER_CLI_BUILD ?= 1
PLATFORM_TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo local)
export PLATFORM_TAG
PLATFORM_DEV ?= 1
export PLATFORM_DEV

.PHONY: help lint lint-fix docker-build docker-up docker-down docker-test docker-app docker-localnet docker-app-test docker-smoke docker-reset docker-clean-cache dev-ui dev-ui-localnet

help:
	@echo "Quality:"
	@echo "  make docker-test      canonical lint + pytest (container, Python 3.14 image)"
	@echo "  make lint             same ruff/vulture scripts inside platform image"
	@echo "  make lint-fix         ruff fix on host (convenience)"
	@echo ""
	@echo "Docker test stack (see docker/README.md):"
	@echo "  make docker-build     build shared platform image once"
	@echo "  make docker-up        Cassandra, Redis, Typesense, migrations"
	@echo "  make docker-app       deps + API + Celery"
	@echo "  make docker-localnet  deps + app + Algorand localnet (algod)"
	@echo "  make docker-app-test  deps + app + pytest (no localnet)"
	@echo "  make docker-smoke     P1: trigger publish + check news feed (needs docker-app)"
	@echo "  make docker-reset     down -v, prune images/volumes"
	@echo ""
	@echo "Full-stack local dev:"
	@echo "  make dev-ui           Docker app (TestNet algod) + Vite SPA"
	@echo "  make dev-ui-localnet  Docker app + private algod + Vite SPA"

lint:
	docker compose build migrate
	docker compose run --rm --no-deps --entrypoint /usr/local/bin/platform-docker/lint.sh migrate

lint-fix:
	ruff check --fix backend workers deploy/scripts
	ruff format backend workers deploy/scripts

docker-build:
	docker compose build migrate

docker-clean-cache:
	rm -rf .cache/buildx-platform .cache/buildx-conduit

docker-up:
	docker compose up -d --wait

docker-down:
	docker compose --profile app --profile test --profile localnet --profile chain down

docker-reset:
	docker compose --profile app --profile test --profile localnet --profile chain down -v
	docker image prune -f
	docker volume prune -f

docker-test:
	./docker/bin/compose-test.sh

docker-app:
	docker compose build migrate
	docker compose --profile app up -d --wait

docker-localnet:
	docker compose build migrate
	docker compose --env-file docker/localnet/.env.example --profile app --profile localnet up -d --wait

docker-app-test:
	COMPOSE_PROFILES=app ./docker/bin/compose-test.sh

docker-smoke:
	./docker/bin/smoke-newspaper.sh

dev-ui:
	./docker/bin/dev-ui.sh

dev-ui-localnet:
	./docker/bin/dev-ui.sh --localnet
