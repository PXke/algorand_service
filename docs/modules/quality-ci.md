# Brick: Quality & CI

## Goal

Catch regressions on every push via tests, lint, and release packaging smoke.

## Status

`done` (v1)

## Features (should do)

- GitHub Actions workflow on push/PR
- Backend pytest (including ARC-0060 and `test_cql_migrate.py` via `pip install -e "./backend[dev]"`)
- Ruff check + `ruff format --check` on `backend/`, `workers/`, `deploy/scripts`
- Vulture dead-code scan (`pyproject.toml` `[tool.vulture]`)
- Shellcheck on `deploy/*.sh` and `deploy/scripts/*.sh`
- Pytest coverage report (`--cov=app`) in CI (no codecov upload yet)
- Local: `make lint` / `make lint-fix`
- Conduit `go test ./...`
- Flutter `dart analyze lib` + `flutter test`
- `docker-test` CI job: `make docker-test` stack (Python **3.14-slim-bookworm** image, lint + pytest in container)
- Weekly/manual release-candidate workflow builds versioned tarball + sha256
- Dependabot for pip, go, and GitHub Actions

## Good to have

- Fail PR if brick doc status table drifts from code (manual review)
- Cache pip and go modules in CI

## Future improvements

- Integration tests with Testcontainers (Cassandra, Redis, Typesense)
- Coverage thresholds and upload to codecov
- E2E: API + worker + chain smoke in CI
- Bandit / Trivy image scanning
- Required status checks before merge to main
- Performance regression bench for chain tail batch size

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| ARC-0060 test vectors | `test_arc0060_verify.py` (requires `py-algorand-sdk`) |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | API contract tests (informative) |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#quality-ci).

## Depends on

- GitHub (or equivalent CI)

## Python version

| Environment | Version | Notes |
|-------------|---------|--------|
| Docker test image | **3.14** | `docker/Dockerfile` → `python:3.14-slim-bookworm` |
| GitHub Actions | **3.14** | `.github/workflows/ci.yml` |
| Host venv (optional) | 3.11+ | `requires-python = ">=3.11"` in `backend/` and `workers/` |

Run the canonical test suite on 3.14: `make docker-test`.

## Code map

- `.github/workflows/ci.yml`
- `.github/workflows/release-candidate.yml`
- `.github/dependabot.yml`
- `docker/Dockerfile`
- `backend/tests/`
