import { describe, expect, it } from 'vitest'

const catalogs = import.meta.glob<{ default: Record<string, string> }>('./locales/*.json', {
  eager: true,
})

describe('locale catalogs', () => {
  it('all locale files carry exactly the same key set as en.json', () => {
    const files = Object.keys(catalogs).sort()
    expect(files.length).toBe(9)
    const en = catalogs['./locales/en.json']?.default
    expect(en).toBeDefined()
    const enKeys = Object.keys(en!).sort()
    for (const f of files) {
      expect(Object.keys(catalogs[f].default).sort(), f).toEqual(enKeys)
    }
  })
})
