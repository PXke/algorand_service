# Publish classifier

Two-tier relevance separates **storage** from **feed publish**.

## Storage tier

`score_content_for_storage()` counts Algorand-related signals. Content is stored in `articles_by_id` when `storage_score >= MIN_STORAGE_SCORE` (default 5). Lower scores only update `domain_tracking` (`is_relevant=false`).

## Publish tier

`is_publish_worthy(text, url, category)` returns:

- `True` — enqueue newspaper publish pipeline
- `False` — stored but not auto-published
- `None` — manual review (`classifier_review_queue`)

### Confidence and sampling

| Variable | Default | Effect |
|----------|---------|--------|
| `CLASSIFIER_CONFIDENCE_THRESHOLD` | `0.8` | Below → review |
| `CLASSIFIER_SAMPLING_THRESHOLD` | `0.0` | Random review rate for confident predictions (`0`=none, `1`=all) |

Model path: `PUBLISH_CLASSIFIER_MODEL_PATH` (RandomForest, retrained daily via `retrain_publish_classifier`).

## Admin feedback

- `POST /api/v1/admin/classifier-feedback` — publish verdict + category + quality
- `GET /api/v1/admin/classifier-reviews` — pending review queue
- Flutter admin tab **Classifier**

Each review captures three dimensions:

| Dimension | Values | Purpose |
|-----------|--------|---------|
| **Publish** | Approve / Reject | Train publish classifier (`approved`) |
| **Category** | `service`, `news`, `tool`, `payment`, `nft`, `governance`, `generic` | Correct auto-categorization; updates `domain_tracking` and article tags |
| **Quality** | `high`, `medium`, `low`, `spam` | Content quality label for retrain weighting and domain metadata |

Feedback rows live in `classifier_feedback` (migrations 018, 021). Retrain task: `app.tasks.crawler.retrain_publish_classifier` (publish label requires `approved` and quality `high` or `medium`).
