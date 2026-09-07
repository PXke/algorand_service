import { describe, expect, it } from 'vitest'
import { hashtagFor, shareHashtags, shareIntentUrl } from './share'

describe('hashtagFor', () => {
  it('keeps the taxonomy casing and joins multi-word labels', () => {
    expect(hashtagFor('defi')).toBe('DeFi')
    expect(hashtagFor('nfts')).toBe('NFTs')
    expect(hashtagFor('smart-contracts')).toBe('SmartContracts')
    expect(hashtagFor('developer-tools')).toBe('DeveloperTools')
  })

  it('returns empty for labels that cannot be hashtags', () => {
    expect(hashtagFor('')).toBe('')
    expect(hashtagFor('2026')).toBe('')
    expect(hashtagFor('---')).toBe('')
  })
})

describe('shareHashtags', () => {
  it('leads with Algorand, drops meta tags, dedupes and caps at three topics', () => {
    expect(
      shareHashtags(['web', 'defi', 'algorand', 'DeFi', 'nfts', 'staking', 'governance', 'updated']),
    ).toEqual(['Algorand', 'DeFi', 'NFTs', 'Staking'])
  })

  it('is just the site tag when there are no tags', () => {
    expect(shareHashtags(null)).toEqual(['Algorand'])
    expect(shareHashtags([])).toEqual(['Algorand'])
    expect(shareHashtags(['web', 'update'])).toEqual(['Algorand'])
  })
})

describe('shareIntentUrl', () => {
  const target = {
    url: 'https://algorand.pxke.me/news/articles/x402-a-b',
    title: 'Fees & votes: 100% #onchain + more',
    tags: ['defi', 'smart-contracts', 'web'],
  }

  it('builds a well-formed X intent with a hashtags parameter', () => {
    const u = new URL(shareIntentUrl('x', target))
    expect(u.origin + u.pathname).toBe('https://x.com/intent/post')
    expect(u.searchParams.get('text')).toBe(target.title)
    expect(u.searchParams.get('url')).toBe(target.url)
    expect(u.searchParams.get('hashtags')).toBe('Algorand,DeFi,SmartContracts')
    // Nothing leaked un-encoded into the query string.
    expect(u.search).not.toMatch(/[ #]/)
  })

  it('puts hashtags in the text for Bluesky and Telegram', () => {
    const bsky = new URL(shareIntentUrl('bluesky', target))
    expect(bsky.origin + bsky.pathname).toBe('https://bsky.app/intent/compose')
    expect(bsky.searchParams.get('text')).toBe(
      `${target.title}\n\n${target.url}\n\n#Algorand #DeFi #SmartContracts`,
    )
    const tg = new URL(shareIntentUrl('telegram', target))
    expect(tg.origin + tg.pathname).toBe('https://t.me/share/url')
    expect(tg.searchParams.get('url')).toBe(target.url)
    expect(tg.searchParams.get('text')).toBe(`${target.title}\n#Algorand #DeFi #SmartContracts`)
  })
})
