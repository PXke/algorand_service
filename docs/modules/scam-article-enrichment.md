# Scam article enrichment (planned)

## Problem

A raw Discord `@everyone` warning is enough to **detect** a scam alert, but not enough to **write** a good article. Sending only that text to Mistral produces a thin rewrite. Worse, the alert might itself be social engineering.

Example (Foundation, May 2026):

> DO NOT interact with **algoblow.com** — malicious app, rekey in transaction requests…

## Goal

Before compose/publish on `scam_alert` / breaking tier:

1. **Extract** domains and URLs from the alert (`algoblow.com`).
2. **Cross-reference** data we already have (prior articles, metrics, registry).
3. **Safely gather** external context (search API, allowlisted fetch of *public* info — not executing wallet flows).
4. Pass a **verification bundle** to Mistral/template so the article explains the threat with evidence.

## Phases

| Phase | Status | Work |
|-------|--------|------|
| P0 | **done** | Topic = `scam_alert`, breaking tier, phrase detection |
| P1 | **stub** | `scam_enrichment.py` — URL/domain extraction only |
| P2 | | Typesense/search: “have we mentioned this domain before?” |
| P3 | | Allowlisted HTTP fetch (robots, no JS execution) for named domain landing page text |
| P4 | | Search API (e.g. programmable search) — “algoblow.com scam algorand” |
| P5 | | Mistral compose prompt includes `format_enrichment_for_prompt()` block |

## Safety rules

- **Never** auto-publish solely from text that says “sign this tx”.
- **Never** fetch or render wallet-connect flows on worker.
- **Do not** crawl malicious sites with logged-in browser sessions.
- Prefer Foundation **push** as source of truth; enrichment is **evidence**, not replacement.

## Code

- `workers/app/modules/newspaper/scam_enrichment.py`
- Full bundle: [writer-enrichment.md](writer-enrichment.md) (wired in `publish_from_queued_row`)

### Example X post

Include in Discord/Telegram mirror or push `page_text`:

`https://x.com/d13_co/status/2060386210732761317`

With `WRITER_ENRICHMENT_FETCH_TWEETS=1`, oEmbed adds @D13’s algoblow scam warning to the Mistral prompt automatically.

## Enable enrichment fetch (future)

```bash
SCAM_ENRICHMENT_ENABLED=1
SCAM_ENRICHMENT_SEARCH_API_KEY=...
```
