# Brick: Algorand page classifier

## Goal

Decide if a page is in-scope for search indexing (Algorand-related, ALGO-payable, etc.).

## Status

`partial` (heuristic scorer implemented; ingest gating deferred until `page-crawl-index`)

## Features (should do)

- Score page text/URL for “Algorand ecosystem relevance”
- Reject obvious off-topic / “Algo” word-salad noise
- Gate Typesense ingest for crawled pages (not newspaper articles tied to registry)

## Scoring rules (v1 heuristic)

| Signal | Weight | Notes |
|--------|--------|-------|
| Known ecosystem domain | +0.45 | `algorand.foundation`, `perawallet.app`, etc. |
| Positive keywords | +0.08 each (max 0.5) | `algorand`, `asa`, `arc-`, `testnet`, … |
| Exact `algorand` in text | +0.15 | Case-insensitive |
| Reject patterns without keywords | −0.25 | `algorithm`, `algebra`, `algonquin` |
| Threshold | ≥ 0.35 | `in_scope=true` |

## Example

```python
from app.modules.search.classifier.score import score_page

result = score_page(
    url="https://algorand.foundation/about",
    text="Algorand pure proof of stake and ALGO on TestNet.",
)
assert result.in_scope
print(result.score, result.reasons)
```

## Good to have

- Explainable features (keywords, known domain list) — **done in v1** via `reasons`
- Human override table in Cassandra

## Future improvements

- Small fine-tuned model trained on labeled ecosystem pages
- Active learning from moderator clicks
- Integration with Playwright crawl brick
- Multilingual support
- Wire into `typesense-indexer` when `page-crawl-index` lands

## Standards & RFCs

**TBD** before ML phase — document data/ML ethics and any Algorand labeling conventions here. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#algorand-page-classifier).

## Depends on

- Future `page-crawl-index` brick, `typesense-indexer`

## Code map

- `backend/app/modules/search/classifier/score.py`
- `backend/tests/test_page_classifier.py`
