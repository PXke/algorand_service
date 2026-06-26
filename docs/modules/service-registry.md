# Brick: Service registry

## Goal

Map on-chain signals (address, app id, asset id) to ecosystem **services** and scrape URLs.

## Status

`done`

## Features (should do)

- Cassandra table `service_registry` with `match_kind`, `match_value`, `scrape_url`, `enabled`
- Load enabled rows for chain tail matching
- Match `pay` receiver/sender, `appl` app id, `axfer` asset id from `txn_json` / columns
- Seed script from `deploy/seeds/testnet_services.toml`
- Backend `match_services_for_transaction()` for tests

## Good to have

- Validation: `scrape_url` must be HTTPS in prod
- Duplicate detection on `match_value` per kind

## Future improvements

- Admin CRUD API + UI for registry
- Match on contract method selectors, ASA config
- Service groups / tags for feed filtering
- Effective-dated rows (enable from round X)
- Import registry from ecosystem YAML maintained by community

## Standards & RFCs

Algorand address encoding (base32 checksum). Internal `match_kind` schema. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#service-registry).

## Depends on

- `cassandra-schema-migrations` (app `003`–`004`)

## Code map

- `backend/app/modules/registry/`
- `workers/app/modules/chain_tail/registry_cache.py`
- `deploy/scripts/seed_service_registry.py`
