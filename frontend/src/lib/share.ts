/* Share-intent URLs for the ShareBar. One place builds every network's
   link so the encoding, the hashtag derivation and the X intent host are
   not restated per button. */
import { displayTagLabel, orderReaderTags, isMetaTag } from './tags'

export type ShareKind = 'x' | 'bluesky' | 'telegram'

export interface ShareTarget {
  /** Absolute URL of the page being shared. */
  url: string
  title: string
  /** The article's raw tag slugs; meta/provenance tags are dropped. */
  tags?: readonly string[] | null
}

/** The site's own hashtag — always first, whatever the article is tagged. */
export const SITE_HASHTAG = 'Algorand'

/** Topic hashtags after the site one; more reads as spam on X. */
const MAX_TOPIC_HASHTAGS = 3

/** One hashtag word from a tag slug, via its reader-facing label so the
    casing the taxonomy chose survives: "defi" -> "DeFi", "smart-contracts"
    -> "SmartContracts", "nfts" -> "NFTs". Empty when nothing usable is left
    (punctuation-only or all-digit labels are not hashtags). */
export function hashtagFor(tag: string): string {
  const label = displayTagLabel(tag)
  const words = label.split(/[^\p{L}\p{N}]+/u).filter(Boolean)
  const joined = words
    .map((w) => (/[A-Z]/.test(w.slice(1)) ? w : w.charAt(0).toUpperCase() + w.slice(1)))
    .join('')
  if (!joined || /^\p{N}+$/u.test(joined)) return ''
  return joined
}

/** Hashtags for a share: the site tag, then up to three topical ones in the
    order the article shows them, de-duplicated case-insensitively. Meta tags
    (provenance chips like "web", "update") never become hashtags. */
export function shareHashtags(tags: readonly string[] | null | undefined): string[] {
  const out = [SITE_HASHTAG]
  const seen = new Set([SITE_HASHTAG.toLowerCase()])
  for (const tag of orderReaderTags(tags ? [...tags] : [])) {
    if (isMetaTag(tag)) continue
    const hashtag = hashtagFor(tag)
    if (!hashtag || seen.has(hashtag.toLowerCase())) continue
    seen.add(hashtag.toLowerCase())
    out.push(hashtag)
    if (out.length > MAX_TOPIC_HASHTAGS) break
  }
  return out
}

function hashtagLine(hashtags: readonly string[]): string {
  return hashtags.map((h) => `#${h}`).join(' ')
}

/** The intent URL a share button opens. Every free-text piece goes through
    encodeURIComponent, so titles with `&`, `#` or `+` survive the query
    string intact. */
export function shareIntentUrl(kind: ShareKind, target: ShareTarget): string {
  const url = encodeURIComponent(target.url)
  const hashtags = shareHashtags(target.tags)
  if (kind === 'x') {
    // X's intent takes hashtags as their own comma-separated parameter (no
    // leading `#`), which it renders as real hashtags in the composer.
    return (
      `https://x.com/intent/post?text=${encodeURIComponent(target.title)}` +
      `&url=${url}&hashtags=${encodeURIComponent(hashtags.join(','))}`
    )
  }
  if (kind === 'bluesky') {
    // Bluesky's composer has no hashtag parameter; they ride in the text.
    const text = `${target.title}\n\n${target.url}\n\n${hashtagLine(hashtags)}`
    return `https://bsky.app/intent/compose?text=${encodeURIComponent(text)}`
  }
  const text = `${target.title}\n${hashtagLine(hashtags)}`
  return `https://t.me/share/url?url=${url}&text=${encodeURIComponent(text)}`
}
