// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { sanitizeArticleHtml } from './sanitizeHtml'

describe('sanitizeArticleHtml', () => {
  it('strips an onerror handler off an img tag', () => {
    const out = sanitizeArticleHtml('<p>lead<img src=x onerror=alert(1)></p>')
    expect(out).not.toMatch(/onerror/i)
    expect(out).not.toMatch(/alert\(1\)/)
  })

  it('strips a javascript: href', () => {
    const out = sanitizeArticleHtml('<a href="javascript:alert(1)">click</a>')
    expect(out).not.toMatch(/javascript:/i)
    expect(out).toContain('click')
  })

  it('strips a javascript: href hidden behind control characters', () => {
    const out = sanitizeArticleHtml('<a href="jav&#9;ascript:alert(1)">click</a>')
    expect(out).not.toMatch(/javascript:/i)
  })

  it('strips an onload handler off an svg tag', () => {
    const out = sanitizeArticleHtml('<svg onload="alert(1)"><circle r="3"></circle></svg>')
    expect(out).not.toMatch(/onload/i)
  })

  it('strips an iframe entirely', () => {
    const out = sanitizeArticleHtml('<p>before</p><iframe src="https://evil.example"></iframe><p>after</p>')
    expect(out).not.toMatch(/iframe/i)
    expect(out).toContain('before')
    expect(out).toContain('after')
  })

  it('strips a data: URL image (data: is forbidden everywhere, not just javascript:)', () => {
    const out = sanitizeArticleHtml('<img src="data:image/png;base64,AAAA">')
    expect(out).not.toMatch(/data:/i)
  })

  it('strips a data: URL disguised as an html document', () => {
    const out = sanitizeArticleHtml(
      '<a href="data:text/html,<script>alert(1)</script>">click</a>',
    )
    expect(out).not.toMatch(/data:/i)
    expect(out).not.toMatch(/<script/i)
  })

  it('leaves ordinary markdown-derived markup unchanged', () => {
    const html =
      '<p class="lede">Hello <strong>world</strong> and <em>friends</em>.</p>' +
      '<h2>Heading</h2><ul><li>one</li><li>two</li></ul>' +
      '<blockquote><p>quoted</p></blockquote>' +
      '<pre><code>const x = 1</code></pre>' +
      '<table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>'
    expect(sanitizeArticleHtml(html)).toBe(html)
  })

  it('leaves a plain http(s) link with target/rel unchanged', () => {
    const html = '<a target="_blank" rel="noopener noreferrer" href="https://example.com">ext</a>'
    expect(sanitizeArticleHtml(html)).toBe(html)
  })

  it('leaves a mailto link unchanged', () => {
    const html = '<a href="mailto:hi@example.com">mail</a>'
    expect(sanitizeArticleHtml(html)).toBe(html)
  })

  it('leaves a glossary auto-linker link (relative href + data-glossary-slug) unchanged', () => {
    const html =
      '<a class="glossary-term" data-glossary-slug="apy" href="/glossary/apy" title="Annual Percentage Yield">APY</a>'
    expect(sanitizeArticleHtml(html)).toBe(html)
  })

  it('leaves an https image plate (figure/figcaption) unchanged', () => {
    const html =
      '<figure class="plate"><img src="https://cdn.example.com/y.png" alt="alt text" loading="lazy" decoding="async"><figcaption>a caption</figcaption></figure>'
    expect(sanitizeArticleHtml(html)).toBe(html)
  })

  it('leaves a restyled sources <details> block unchanged', () => {
    const html =
      '<details class="cite-block"><summary class="cite-summary"><h2>Sources</h2> ' +
      '<span class="cite-count">(1)</span></summary><ul class="cite-list">' +
      '<li class="cite"><a href="https://example.com" target="_blank" rel="noopener noreferrer">' +
      '<span class="cite-host">example.com</span></a></li></ul></details>'
    expect(sanitizeArticleHtml(html)).toBe(html)
  })

  it('leaves an SVG chart (chartRender.ts output) unchanged', () => {
    const html =
      '<figure class="chart-figure"><figcaption class="chart-title">Volume</figcaption>' +
      '<svg viewBox="0 0 640 320" class="chart-svg" role="img" aria-label="Volume">' +
      '<line x1="52" y1="1" x2="2" y2="1" class="chart-baseline"></line>' +
      '<rect x="1" y="1" width="2" height="2" style="fill:var(--accent)" rx="2">' +
      '<title>Series A: 1</title></rect></svg></figure>'
    expect(sanitizeArticleHtml(html)).toBe(html)
  })

  it('returns an empty string for empty input', () => {
    expect(sanitizeArticleHtml('')).toBe('')
  })
})
