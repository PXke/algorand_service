# Backend tests

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" --index-url https://pypi.org/simple
# ARC-0060 vector test needs py-algorand-sdk (included in [dev] / main deps)
PYTHONPATH=. pytest -q

# Or with Docker deps only: see ../docker/README.md
# docker compose up -d && docker compose --profile test run --rm test
```

| File | Covers |
|------|--------|
| `test_chain_verify.py` | On-chain submission rules |
| `test_chain_repository.py` | Cassandra row mapping (mocked) |
| `test_suggestion_service.py` | Suggestion create + txid verification |
| `test_siwa_message.py` | SIWA message format |
| `test_arc0060_verify.py` | ARC-0060 reference vector (needs algosdk) |
