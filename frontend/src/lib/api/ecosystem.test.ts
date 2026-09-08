import { describe, expect, it } from 'vitest'
import {
  ECOSYSTEM_CATEGORIES,
  ECOSYSTEM_MAX_TAGS,
  ecosystemApi,
  ecosystemCategoryLabel,
  stripMarkdownToText,
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

describe('ECOSYSTEM_CATEGORY_LABELS', () => {
  it('names every category with a human phrase, and falls back to the slug for unknown values', () => {
    for (const slug of ECOSYSTEM_CATEGORIES) {
      const label = ecosystemCategoryLabel(slug)
      expect(label).not.toBe('')
      expect(label).not.toBe(slug)
    }
    expect(ecosystemCategoryLabel('not-a-category')).toBe('not-a-category')
  })
})

describe('ECOSYSTEM_MAX_TAGS', () => {
  it('matches the backend bound (design doc section 3.1: up to 5 free tags)', () => {
    expect(ECOSYSTEM_MAX_TAGS).toBe(5)
  })
})

describe('stripMarkdownToText', () => {
  it('turns a link into its link text', () => {
    expect(stripMarkdownToText('See [our docs](https://example.test) for more.')).toBe(
      'See our docs for more.',
    )
  })

  it('turns an image into its alt text', () => {
    expect(stripMarkdownToText('![a logo](https://example.test/logo.png)')).toBe('a logo')
  })

  it('strips emphasis and bold markers', () => {
    expect(stripMarkdownToText('This is **bold** and this is *italic*.')).toBe(
      'This is bold and this is italic.',
    )
  })

  it('strips heading markers', () => {
    expect(stripMarkdownToText('## What it does\nA wallet for Algorand.')).toBe(
      'What it does A wallet for Algorand.',
    )
  })

  it('strips bullet markers and collapses multi-line lists to one line', () => {
    expect(stripMarkdownToText('- one\n- two\n- three')).toBe('one two three')
  })

  it('strips inline and fenced code', () => {
    expect(stripMarkdownToText('Run `npm install` then:\n\n```\nnpm start\n```')).toBe(
      'Run npm install then:',
    )
  })

  it('collapses multiple paragraphs into one line', () => {
    expect(stripMarkdownToText('Para one.\n\nPara two.\n\nPara three.')).toBe(
      'Para one. Para two. Para three.',
    )
  })

  it('leaves plain text with no markdown syntax unchanged', () => {
    expect(stripMarkdownToText('A plain one-sentence description.')).toBe(
      'A plain one-sentence description.',
    )
  })
})
