/* Provenance / pipeline labels — fine as chips, bad as the lead kicker.
   Generated from shared/taxonomy.json, which the Python SSR reads too: this
   list and the backend's used to differ, so an article tagged
   ["update", "defi"] was served to crawlers under "Update" and re-labelled
   "DeFi" the moment the SPA hydrated. */
import { META_TAGS, DISPLAY_LABELS } from './taxonomy.generated'

export function isMetaTag(tag: string): boolean {
  return META_TAGS.has(tag.trim().toLowerCase())
}

/** Lead with topical tags; keep provenance/meta chips at the end. */
export function orderReaderTags(tags: string[] | null | undefined): string[] {
  const topical: string[] = []
  const meta: string[] = []
  const seen = new Set<string>()
  for (const raw of tags ?? []) {
    const tag = String(raw ?? '')
      .trim()
      .toLowerCase()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    if (META_TAGS.has(tag)) meta.push(tag)
    else topical.push(tag)
  }
  return [...topical, ...meta]
}

/** Best tag for kickers / related-topic / older-newer navigation. */
export function primaryTopic(tags: string[] | null | undefined): string | null {
  const ordered = orderReaderTags(tags)
  return ordered.find((t) => !META_TAGS.has(t)) ?? ordered[0] ?? null
}

/** Reader-facing text for a tag slug. Display only — /topic/<tag> links keep
    the raw slug, so a URL never changes shape based on this table. The SSR
    applies the same map; without it the server said "on-chain" and the SPA
    said "chain-only" on the same chip. */
export function displayTagLabel(tag: string | null | undefined): string {
  const key = String(tag ?? '')
    .trim()
    .toLowerCase()
  return DISPLAY_LABELS[key] ?? String(tag ?? '')
}

/* ------------------------------------------------------------------ *
 * Section colour — four desks
 *
 * Like a print paper's desks (Business / Tech / Culture / Politics), every
 * topic belongs to one of four sections, plus a neutral for anything we
 * haven't classified. The reader learns four colours, not thirty.
 *
 * Four is not an aesthetic choice, it is the measured ceiling. An earlier
 * eight-hue set failed the dataviz palette validator badly (rust↔amber
 * ΔE 2.0 under deuteranopia, green↔teal ΔE 6.6 for normal vision): you
 * cannot hold eight hues at one lightness and keep them apart. Four clears
 * every check. Unclassified tags go neutral rather than being hashed onto a
 * desk — an arbitrary desk would be a lie about the content.
 * ------------------------------------------------------------------ */

type Tone = 'markets' | 'protocol' | 'assets' | 'people' | 'alert' | 'meta'

const TONE_BY_TOPIC: Record<string, Tone> = {
  // Markets — money moving
  defi: 'markets',
  lending: 'markets',
  pricing: 'markets',
  price: 'markets',
  market: 'markets',
  markets: 'markets',
  stablecoin: 'markets',
  payments: 'markets',
  treasury: 'markets',
  liquidity: 'markets',
  trading: 'markets',
  'swap-aggregator': 'markets',
  institutional: 'markets',
  compliance: 'markets',
  // Protocol — the chain and the tools on it
  infrastructure: 'protocol',
  api: 'protocol',
  sdk: 'protocol',
  'developer-tools': 'protocol',
  'smart-contracts': 'protocol',
  testnet: 'protocol',
  tooling: 'protocol',
  node: 'protocol',
  staking: 'protocol',
  consensus: 'protocol',
  validators: 'protocol',
  rewards: 'protocol',
  decentralization: 'protocol',
  'post-quantum': 'protocol',
  'post-quantum-cryptography': 'protocol',
  security: 'protocol',
  audit: 'protocol',
  privacy: 'protocol',
  explorer: 'protocol',
  'cross-chain': 'protocol',
  validator: 'protocol',
  'open-source': 'protocol',
  algokit: 'protocol',
  agents: 'protocol',
  'arc-standards': 'protocol',
  avm: 'protocol',
  // Assets — things issued and traded
  nft: 'assets',
  marketplace: 'assets',
  tokenization: 'assets',
  asa: 'assets',
  collectibles: 'assets',
  gaming: 'assets',
  memecoins: 'assets',
  launchpad: 'assets',
  // People — who is doing it and how they decide
  identity: 'people',
  nfd: 'people',
  community: 'people',
  recap: 'people',
  wallet: 'people',
  social: 'people',
  governance: 'people',
  dao: 'people',
  voting: 'people',
  foundation: 'people',
  directory: 'people',
  education: 'people',
  undp: 'people',
  partnership: 'people',
  xgov: 'people',
  humanitarian: 'people',
  afghanistan: 'people',
  onboarding: 'people',
  // Alert — urgency, not a desk. These are the labels the pipeline stamps
  // deterministically (article_tags.py `_TOPIC_TAGS` / the breaking tier),
  // and every one of them used to fall through to neutral grey — the desk
  // system went colourless on exactly the stories that matter most.
  breaking: 'alert',
  'scam-alert': 'alert',
  outage: 'alert',
  exploit: 'alert',
  incident: 'alert',
}

/** Desk for a topic; unclassified topics read neutral, never a guessed desk. */
export function topicTone(tag: string | null | undefined): Tone {
  const key = String(tag ?? '')
    .trim()
    .toLowerCase()
  if (!key) return 'meta'
  return TONE_BY_TOPIC[key] ?? 'meta'
}

/** CSS colour for a topic, for inline `style="color: …"`. */
export function topicColor(tag: string | null | undefined): string {
  return `var(--tone-${topicTone(tag)})`
}
