# Content categorization

Web discovery content is labeled before publish classification.

## Categories

`service`, `news`, `tool`, `payment`, `nft`, `governance`, `generic`

## Implementation

`workers/app/modules/ai/content_categorizer.py`:

1. When `CONTENT_CATEGORIZATION_ENABLED=1` and Mistral is configured, a short chat prompt returns the category.
2. Otherwise keyword heuristics map content to a category.

Categories are stored on `domain_tracking.category` and passed to `is_publish_worthy()`.

## Configuration

| Variable | Default |
|----------|---------|
| `CONTENT_CATEGORIZATION_ENABLED` | `1` |
| `MISTRAL_ENABLED` | `0` (optional LLM path) |
