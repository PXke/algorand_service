// Static, per-product storefront content: which i18n keys describe each
// product, how to call it (curl + the pxke_x402 Python client), and a
// realistic example of what comes back. Prices, routes, status and payTo
// are NEVER here -- they are read live from the catalog (lib/x402/catalog.ts).
//
// Request fields, limits and example responses mirror the backend route
// modules exactly (x402_scan/api/routes.py, x402_storage/api/routes.py,
// x402_news/api/routes.py) -- keep them in step by hand when a route changes.
import { X402_PATHS } from '../api/x402'
import type { X402ProductKey } from './catalog'

export type X402Snippets = { curl: string; python: string }

export type X402ProductDef = {
  key: X402ProductKey
  path: '/scan' | '/storage' | '/news'
  /** Catalog path of the route the page leads with (the one the snippets call). */
  leadPath: string
  leadMethod: string
  nameKey: string
  pitchKey: string
  whoKey: string
  limitKeys: readonly string[]
  snippets: (apiBase: string) => X402Snippets
  /** A realistic example response of the lead route, as the backend's own output example describes it. */
  example: unknown
}

const SCAN_EXAMPLE = {
  source_url: 'https://example.com/suspicious-download.zip',
  download_bytes: 18422,
  schema_version: 1,
  status: 'ok',
  one_line_summary: 'No concerns found -- no indicators -- file type: Zip archive data',
  target: {
    label: 'input',
    size_bytes: 18422,
    type: 'Zip archive data',
    entropy_bits_per_byte: 7.91,
    indicators: { urls: [], ipv4_addresses: [] },
  },
  clamav: { engine: 'clamdscan', exit_code: 0, infected_files: [], clean: true },
  archive: {
    archive_kind: 'zip',
    member_count: 3,
    declared_uncompressed_bytes: 40200,
    extraction_skipped_reason: null,
    clamav: { engine: 'clamdscan', exit_code: 0, infected_files: [], clean: true },
    members: [],
  },
  yara: { matched_rules: 0 },
  fuzzy_hash: {
    ssdeep_hash: '3:hMCE0O++uV4d3QOMikMR+RFgVn:hu0O0u29MRuFgV',
    comparison_corpus: null,
    note: 'no known-bad comparison corpus is bundled -- informational only, never contributes to risk_score',
  },
  risk: { score: 0.0, verdict: 'no concerns found', malicious: false, caution_notes: [] },
  settlement_tx_id: 'JQ3G…R5YA',
}

const STORAGE_EXAMPLE = {
  backup_id: '7f3a9c2e-5b1d-4e8f-9a6c-2d4b8e1f0a3c',
  size_bytes: 24576,
  content_hash: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
  label: 'my-agent-state-backup',
  created_at_epoch: 1788968366,
  expires_at_epoch: 1796744366,
  status: 'active',
  settlement_tx_id: 'JQ3G…R5YA',
  current_version: 1,
}

const NEWS_EXAMPLE = {
  query: 'tinyman volume',
  engine: 'typesense',
  items: [
    {
      article_id: '6f1c2a4e-3b5d-4c7e-9a1b-2d3e4f5a6b7c',
      slug: 'tinyman-v2-crosses-1b-cumulative-volume',
      title: 'Tinyman v2 crosses $1B cumulative volume',
      summary: 'The Algorand DEX passed the milestone on 2026-08-28.',
      snippet: '... cumulative <mark>volume</mark> on <mark>Tinyman</mark> v2 ...',
      score: 1157451471441100800.0,
      published_at_epoch: 1756377600,
      url: 'https://algorand.pxke.me/news/articles/tinyman-v2-crosses-1b-cumulative-volume',
    },
  ],
  settlement_tx_id: 'JQ3G…R5YA',
}

export const X402_PRODUCTS: Record<X402ProductKey, X402ProductDef> = {
  scan: {
    key: 'scan',
    path: '/scan',
    leadPath: X402_PATHS.scanUrl,
    leadMethod: 'POST',
    nameKey: 'x402ProductScanName',
    pitchKey: 'x402ProductScanPitch',
    whoKey: 'x402ProductScanWho',
    limitKeys: ['x402ScanLimit1', 'x402ScanLimit2', 'x402ScanLimit3', 'x402ScanLimit4'],
    snippets: (apiBase) => ({
      curl: `# 1. Ask for the offer (HTTP 402, offer in the PAYMENT-REQUIRED header)
curl -si -X POST '${apiBase}${X402_PATHS.scanUrl}' \\
  -H 'Content-Type: application/json' \\
  -d '{"url": "https://example.com/suspicious-download.zip"}'

# 2. Sign an Algorand transfer for that offer with any x402 client, then
# 3. repeat the same request with the signed group attached
curl -s -X POST '${apiBase}${X402_PATHS.scanUrl}' \\
  -H 'Content-Type: application/json' \\
  -H "PAYMENT-SIGNATURE: $SIGNED" \\
  -d '{"url": "https://example.com/suspicious-download.zip"}'

# Free preview of the response shape, nothing fetched, nothing charged
curl -s -X POST '${apiBase}${X402_PATHS.scanUrl}?preview=true' \\
  -H 'Content-Type: application/json' \\
  -d '{"url": "https://example.com/suspicious-download.zip"}'`,
      python: `from pxke_x402 import PxkeClient

client = PxkeClient(mnemonic=MNEMONIC)  # 25 words, the paying wallet

report = client.scan_url("https://example.com/suspicious-download.zip")
print(report["one_line_summary"])
print(report["risk"]["verdict"], report["risk"]["malicious"])
for member in (report.get("archive") or {}).get("members", []):
    print(member)`,
    }),
    example: SCAN_EXAMPLE,
  },
  storage: {
    key: 'storage',
    path: '/storage',
    leadPath: X402_PATHS.storageBackups,
    leadMethod: 'POST',
    nameKey: 'x402ProductStorageName',
    pitchKey: 'x402ProductStoragePitch',
    whoKey: 'x402ProductStorageWho',
    limitKeys: ['x402StorageLimit1', 'x402StorageLimit2', 'x402StorageLimit3', 'x402StorageLimit4'],
    snippets: (apiBase) => ({
      curl: `# Store: declare the size so the price is fixed before the body is read
DATA=$(base64 -w0 state.json); SIZE=$(stat -c%s state.json)
curl -si -X POST "${apiBase}${X402_PATHS.storageBackups}?declared_size_bytes=$SIZE&retention_days=90" \\
  -H 'Content-Type: application/json' \\
  -d "{\\"data\\": \\"$DATA\\", \\"label\\": \\"my-agent-state-backup\\"}"
# -> 402 with the offer; sign it, resend the same request with PAYMENT-SIGNATURE

# Read back (free): ask for a nonce, sign it with the same wallet, present it once
curl -s -X POST '${apiBase}${X402_PATHS.storageChallenge}' \\
  -H 'Content-Type: application/json' -d '{"wallet": "'$WALLET'"}'
curl -s "${apiBase}${X402_PATHS.storageBackups}/$BACKUP_ID?wallet=$WALLET&nonce=$NONCE&proof_method=signed_bytes&signature_b64=$SIG"`,
      python: `from pxke_x402 import PxkeClient

client = PxkeClient(mnemonic=MNEMONIC)

with open("state.json", "rb") as f:
    blob = f.read()                       # encrypt first if it is sensitive

backup = client.storage_create_backup(blob, label="my-agent-state-backup")
print(backup["backup_id"], backup["expires_at_epoch"])

# Later: add a version under the same id, or extend the retrieval window
client.storage_renew_backup(backup["backup_id"], wallet=client.address)`,
    }),
    example: STORAGE_EXAMPLE,
  },
  news: {
    key: 'news',
    path: '/news',
    leadPath: X402_PATHS.newsSearch,
    leadMethod: 'GET',
    nameKey: 'x402ProductNewsName',
    pitchKey: 'x402ProductNewsPitch',
    whoKey: 'x402ProductNewsWho',
    limitKeys: ['x402NewsLimit1', 'x402NewsLimit2', 'x402NewsLimit3'],
    snippets: (apiBase) => ({
      curl: `# Free: headlines, the tag taxonomy, one article in full
curl -s '${apiBase}${X402_PATHS.news}?limit=10&tag=defi&lang=fr'
curl -s '${apiBase}${X402_PATHS.newsTags}?limit=50'
curl -s '${apiBase}${X402_PATHS.newsArticle}/tinyman-v2-crosses-1b-cumulative-volume'

# Paid: ranked search. First call answers 402 with the offer...
curl -si '${apiBase}${X402_PATHS.newsSearch}?q=tinyman%20volume&limit=10'
# ...sign it, then repeat with the signed group attached
curl -s '${apiBase}${X402_PATHS.newsSearch}?q=tinyman%20volume&limit=10' \\
  -H "PAYMENT-SIGNATURE: $SIGNED"`,
      python: `from pxke_x402 import PxkeClient

client = PxkeClient(mnemonic=MNEMONIC)   # free reads work without one

for item in client.news(tag="defi", limit=10)["items"]:
    print(item["published_at_epoch"], item["title"])

hits = client.search_news("tinyman volume", limit=10)   # paid per query
for hit in hits["items"]:
    article = client.read_article(hit["slug"])          # free, full body
    print(hit["score"], article["title"])`,
    }),
    example: NEWS_EXAMPLE,
  },
}

export function productDef(key: X402ProductKey): X402ProductDef {
  return X402_PRODUCTS[key]
}
