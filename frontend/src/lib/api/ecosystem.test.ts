import { describe, expect, it } from 'vitest'
import {
  ECOSYSTEM_CATEGORIES,
  ECOSYSTEM_MAX_TAGS,
  ecosystemApi,
} from './ecosystem'

describe('ecosystemApi.badgeUrl', () => {
  it('builds the badge SVG route for a slug', () => {
    expect(ecosystemApi.badgeUrl('my-project')).toBe('/api/v1/ecosystem/my-project/badge.svg')
  })

  it('URL-encodes an unusual slug', () => {
    expect(ecosystemApi.badgeUrl('a b')).toBe('/api/v1/ecosystem/a%20b/badge.svg')
  })
})

describe('ecosystemApi.badgeMarkdownSnippet', () => {
  it('embeds the badge image, links to the entry page, and titles the link with the project name', () => {
    const snippet = ecosystemApi.badgeMarkdownSnippet('my-project', 'My Project')
    expect(snippet).toContain('![Listed on PXke Algorand]')
    expect(snippet).toContain('/api/v1/ecosystem/my-project/badge.svg')
    expect(snippet).toContain('https://algorand.pxke.me/registry/my-project')
    expect(snippet).toContain('My Project is listed on the Algorand Open Registry')
  })
})

describe('ECOSYSTEM_CATEGORIES', () => {
  it('matches the backend closed enum: 17 real categories plus "other", no duplicates', () => {
    expect(ECOSYSTEM_CATEGORIES.length).toBe(18)
    expect(new Set(ECOSYSTEM_CATEGORIES).size).toBe(ECOSYSTEM_CATEGORIES.length)
    expect(ECOSYSTEM_CATEGORIES[ECOSYSTEM_CATEGORIES.length - 1]).toBe('other')
  })
})

describe('ECOSYSTEM_MAX_TAGS', () => {
  it('matches the backend bound (design doc section 3.1: up to 5 free tags)', () => {
    expect(ECOSYSTEM_MAX_TAGS).toBe(5)
  })
})
