/** Provenance / pipeline labels — fine as chips, bad as the lead kicker. */
const META_TAGS = new Set([
  'web',
  'chain',
  'chain-only',
  'onchain',
  'on-chain',
  'mail',
  'discord',
  'telegram',
  'update',
  'discovery',
  'news',
  'ai',
  'generic',
  'algorand',
  'updated',
  'weekly',
  'digest',
])

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
  // Assets — things issued and traded
  nft: 'assets',
  marketplace: 'assets',
  tokenization: 'assets',
  asa: 'assets',
  collectibles: 'assets',
  gaming: 'assets',
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

/** Soft background wash matching a topic's tone. */
export function topicWash(tag: string | null | undefined): string {
  return `var(--tone-${topicTone(tag)}-soft)`
}
