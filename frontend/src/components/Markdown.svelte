<script lang="ts">
  import { marked, Renderer } from 'marked'
  import type { Attachment } from 'svelte/attachments'
  import { get } from 'svelte/store'
  import { looksLikeFaviconUrl, proxiedImageUrl, sameImageUrl } from '../lib/images'
  import { renderChartHtml } from '../lib/chartRender'
  import { sanitizeArticleHtml } from '../lib/sanitizeHtml'
  import { glossaryApi, type GlossaryTerm } from '../lib/api/glossary'
  import { activeLocale, messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import Icon from './Icon.svelte'

  let { source = '', skipHref = '' }: { source?: string; skipHref?: string } = $props()

  // Resolved glossary entries are small (a name + a sentence or two) and
  // rarely change mid-session -- cache across every Markdown instance on the
  // page (an article body can link the same term several times, and admin
  // preview tabs re-render on every keystroke) rather than refetching.
  const glossaryEntryCache = new Map<string, GlossaryTerm>()

  let wrapEl: HTMLDivElement | undefined = $state()
  let popoverSlug: string | null = $state(null)
  let popoverPos: { top: number; left: number } | null = $state(null)
  let popoverFallback: { term: string; definition: string } | null = $state(null)
  let popoverEntry: GlossaryTerm | null = $state(null)
  let popoverLoading = $state(false)
  let popoverFailed = $state(false)

  function closeGlossaryPopover(): void {
    popoverSlug = null
    popoverPos = null
    popoverFallback = null
    popoverEntry = null
    popoverLoading = false
    popoverFailed = false
  }

  // Source changes (recompose, admin preview edits, article navigation)
  // invalidate any open popover's anchor position.
  $effect(() => {
    void source
    closeGlossaryPopover()
  })

  $effect(() => {
    if (!popoverSlug) return
    const onPointerDown = (e: PointerEvent) => {
      const node = e.target
      if (node instanceof Node && wrapEl?.contains(node)) return
      closeGlossaryPopover()
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeGlossaryPopover()
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('keydown', onKeyDown)
    }
  })

  async function openGlossaryPopover(anchor: HTMLAnchorElement): Promise<void> {
    const slug = anchor.dataset.glossarySlug
    if (!slug || !wrapEl) return
    const rect = anchor.getBoundingClientRect()
    const wrapRect = wrapEl.getBoundingClientRect()
    popoverSlug = slug
    popoverPos = { top: rect.bottom - wrapRect.top + 8, left: rect.left - wrapRect.left }
    popoverFallback = { term: anchor.textContent ?? '', definition: anchor.title ?? '' }
    popoverFailed = false
    const cached = glossaryEntryCache.get(slug)
    if (cached) {
      popoverEntry = cached
      return
    }
    popoverEntry = null
    popoverLoading = true
    try {
      const entry = await glossaryApi.fetchTerm(slug, get(activeLocale))
      glossaryEntryCache.set(slug, entry)
      if (popoverSlug === slug) popoverEntry = entry
    } catch {
      if (popoverSlug === slug) popoverFailed = true
    } finally {
      if (popoverSlug === slug) popoverLoading = false
    }
  }

  /** Click delegate for the whole rendered body: only glossary term links get
      the popover treatment. A plain left click opens/closes it in place; any
      modified click (new tab, new window, context menu) is left completely
      alone so the underlying page keeps working for anyone who wants it. */
  function onContainerClick(event: MouseEvent): void {
    const target = event.target
    // A click that lands inside the popover itself (its text, padding, or a
    // button that isn't a glossary link) must not fall through to the
    // "close on any other click" branch below -- that branch exists for
    // clicks elsewhere in the article, not for the popover's own content.
    if (target instanceof Element && target.closest('.glossary-popover')) return
    const anchor =
      target instanceof Element ? target.closest<HTMLAnchorElement>('a.glossary-term') : null
    if (!anchor) {
      if (popoverSlug) closeGlossaryPopover()
      return
    }
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return
    }
    event.preventDefault()
    if (popoverSlug === anchor.dataset.glossarySlug) {
      closeGlossaryPopover()
    } else {
      void openGlossaryPopover(anchor)
    }
  }

  function viewFullGlossaryEntry(): void {
    if (!popoverSlug) return
    const slug = popoverSlug
    closeGlossaryPopover()
    navigate(`/glossary/${encodeURIComponent(slug)}`)
  }

  function hostOf(url: string): string {
    try {
      return new URL(url).hostname.replace(/^www\./, '')
    } catch {
      return ''
    }
  }

  function esc(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
  }

  function plateCaption(alt: string, title?: string | null): string {
    const raw = (title || alt || '').trim()
    if (!raw) return ''
    if (/^https?:\/\//i.test(raw)) return ''
    if (/^(image|img|photo|logo)$/i.test(raw)) return ''
    if (/\.(png|jpe?g|gif|webp|svg|avif)$/i.test(raw) && !/\s/.test(raw)) return ''
    return raw
  }

  function plateHtml(href: string, alt: string, title?: string | null): string {
    const src = proxiedImageUrl(href)
    const caption = plateCaption(alt, title)
    const img = `<img src="${esc(src)}" alt="${esc(alt)}" loading="lazy" decoding="async">`
    const cap = caption ? `<figcaption>${esc(caption)}</figcaption>` : ''
    return `<figure class="plate">${img}${cap}</figure>`
  }

  /* Pipeline articles close with "## Sources" (or "References") and a bullet
     list of links. Restyle that list as citation rows: host in mono, title
     after — a wire index, not a stack of underlined URLs. The whole block is
     wrapped in a native <details> — a long source list otherwise runs the
     reader off the end of the article for a section they rarely open. The
     heading tag/attrs (open/close) are kept nested inside <summary> so the
     document outline still sees a heading, same trick used by the admin
     <details> panels (QueueTab's history, AnalyticsTab's ref-group). */
  function restyleSources(html: string): string {
    /* Heading inner is [^<]* so we cannot backtrack across earlier h2s to
       the first <ul> in the article and skip the actual Sources list. */
    return html.replace(
      /(<h[1-6][^>]*>)([^<]*)(<\/h[1-6]>)\s*<ul>([\s\S]*?)<\/ul>/gi,
      (full, open: string, inner: string, close: string, items: string) => {
        const heading = inner.replace(/&nbsp;/g, ' ').trim()
        if (!/^(sources?|references?)$/i.test(heading)) return full
        const rewritten = items.replace(/<li>([\s\S]*?)<\/li>/gi, (_li, liInner: string) => {
          const aMatch = liInner.match(/<a\s[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/i)
          if (!aMatch) return `<li>${liInner}</li>`
          const href = aMatch[1]
          const host = hostOf(href)
          const titleText = aMatch[2].replace(/<[^>]+>/g, '').trim()
          const showTitle =
            Boolean(titleText) &&
            Boolean(host) &&
            !/^https?:\/\//i.test(titleText) &&
            !titleText.toLowerCase().includes(host.toLowerCase())
          const title = showTitle ? `<span class="cite-title">${aMatch[2]}</span>` : ''
          return `<li class="cite"><a href="${href}" target="_blank" rel="noopener noreferrer"><span class="cite-host">${host || titleText}</span>${title}</a></li>`
        })
        const count = (rewritten.match(/<li\b/gi) ?? []).length
        return (
          `<details class="cite-block"><summary class="cite-summary">${open}${inner} ` +
          `<span class="cite-count">(${count})</span>${close}</summary>` +
          `<ul class="cite-list">${rewritten}</ul></details>`
        )
      },
    )
  }

  /* Recreated when `html` changes so newly rendered tables get observed. */
  function markOverflow(html: string): Attachment {
    return (node) => {
      void html
      const frames = node.querySelectorAll<HTMLElement>('.table-frame')
      const sync = (frame: HTMLElement) => {
        const scroller = frame.querySelector<HTMLElement>('.table-scroll')
        if (!scroller) return
        const overflow = scroller.scrollWidth > scroller.clientWidth + 8
        const atEnd =
          scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 8
        frame.classList.toggle('is-overflow', overflow && !atEnd)
      }
      const check = () => {
        for (const frame of frames) sync(frame)
      }
      check()
      const ro = new ResizeObserver(check)
      ro.observe(node)
      const onScroll: Array<() => void> = []
      for (const frame of frames) {
        const scroller = frame.querySelector<HTMLElement>('.table-scroll')
        if (!scroller) continue
        ro.observe(scroller)
        const handler = () => sync(frame)
        scroller.addEventListener('scroll', handler, { passive: true })
        onScroll.push(() => scroller.removeEventListener('scroll', handler))
      }
      return () => {
        ro.disconnect()
        for (const off of onScroll) off()
      }
    }
  }

  function shouldSkipImage(url: string): boolean {
    if (!url || looksLikeFaviconUrl(url)) return true
    return Boolean(skipHref.trim()) && sameImageUrl(url, skipHref)
  }

  /** Drop favicon-only image lines the pipeline sometimes injects as lead art. */
  function cleanSource(raw: string): string {
    return raw.replace(
      /^!\[[^\]]*\]\((\S+?)(?:\s+"[^"]*")?\)\s*$/gm,
      (full, url: string) => (shouldSkipImage(url) ? '' : full),
    )
  }

  /* marked's GFM `del` tokenizer treats a lone `~text~` pair as
     strikethrough, not just the spec's `~~text~~`. Writer prose uses `~`
     for "approximately" (e.g. "~$16"), so two unrelated markers anywhere
     in the same article silently strike through everything between them.
     Escape any tilde that isn't part of a genuine `~~` pair so it stays
     literal. */
  function escapeLoneTildes(raw: string): string {
    return raw.replace(/~/g, (_m, offset: number, str: string) =>
      str[offset - 1] === '~' || str[offset + 1] === '~' ? '~' : '\\~',
    )
  }

  const html = $derived.by(() => {
    void skipHref
    const cleaned = escapeLoneTildes(cleanSource(source || '')).trim()
    if (!cleaned) return ''

    const renderer = new Renderer()
    renderer.image = ({ href, title, text }) => {
      const url = href ?? ''
      if (!url || shouldSkipImage(url)) return ''
      return plateHtml(url, text ?? '', title)
    }
    /* Mark the opening paragraph at parse time. Doing it after render meant
       the lede's font-size landed a frame late — a layout shift on every
       article. Image-only and empty paragraphs are skipped, so a body that
       opens with lead art still tags the first real prose. */
    let ledeSeen = false
    const baseParagraph = renderer.paragraph.bind(renderer)
    renderer.paragraph = (token) => {
      const out = baseParagraph(token)
      const unwrapped = out.replace(
        /^<p>\s*(<figure class="plate">[\s\S]*?<\/figure>)\s*<\/p>$/i,
        '$1',
      )
      if (unwrapped !== out) return unwrapped
      if (ledeSeen) return out
      const bare = out.replace(/<[^>]*>/g, '').trim()
      if (!bare || /<img\b/i.test(out) || /<figure\b/i.test(out)) return out
      ledeSeen = true
      return out.replace('<p>', '<p class="lede">')
    }

    /* Wrap tables so a wide one scrolls instead of being squeezed. Pipeline
       tables run to 8 columns; forced into the reading measure that left ~45px
       a column on a phone, which is not a table anyone can read. The wrapper
       scrolls, and the cell min-width below is what decides when. */
    const baseTable = renderer.table.bind(renderer)
    renderer.table = (token) =>
      `<div class="table-frame"><div class="table-scroll">${baseTable(token)}</div><span class="table-hint" aria-hidden="true">→</span></div>`

    const baseLink = renderer.link.bind(renderer)
    renderer.link = (token) => {
      const { href } = token
      const out = baseLink(token)
      // Open external http(s) links in a new tab.
      if (href && /^https?:\/\//i.test(href)) {
        return out.replace('<a ', '<a target="_blank" rel="noopener noreferrer" ')
      }
      // Glossary auto-linker output (workers glossary_linker.py):
      // `[term](/glossary/slug "definition")`. Tagged with its own class +
      // slug so it can be styled apart from a citation link and, on click,
      // opened as an in-page popover instead of a full navigation (see
      // openGlossaryPopover below) -- the plain href/title stay in the
      // markup so the page still works with JS off or on a modified click.
      const glossaryMatch = href ? href.match(/^\/glossary\/([a-z0-9-]+)\/?$/i) : null
      if (glossaryMatch) {
        return out.replace(
          '<a ',
          `<a class="glossary-term" data-glossary-slug="${esc(glossaryMatch[1])}" `,
        )
      }
      return out
    }

    // The compose pipeline's chart_data tool embeds a ```chart fenced JSON
    // block (see chartRender.ts) — render it as an actual chart instead of
    // falling through to a plain code block showing raw JSON to readers.
    // Anything that fails to parse (malformed JSON, unsupported shape)
    // degrades to the normal code-block rendering rather than breaking the
    // article, since chart data still originates from an LLM.
    const baseCode = renderer.code.bind(renderer)
    renderer.code = (token) => {
      if (token.lang === 'chart') {
        const chart = renderChartHtml(token.text)
        if (chart) return chart
      }
      return baseCode(token)
    }

    const parsed = marked.parse(cleaned, {
      async: false,
      gfm: true,
      breaks: false,
      renderer,
    }) as string
    // Article bodies are writer/LLM output over crawled-page content --
    // never trusted -- so the fully-assembled markup (including the
    // restyled sources block) is sanitized right before it reaches
    // {@html} below, not just the raw marked.parse() output.
    return sanitizeArticleHtml(restyleSources(parsed))
  })

</script>

{#if html}
  <div class="md-wrap" bind:this={wrapEl} onclick={onContainerClick} role="presentation">
    <div class="md" {@attach markOverflow(html)}>
      {@html html}
    </div>
    {#if popoverSlug && popoverPos}
      {@const shown = popoverEntry ?? popoverFallback}
      <div
        class="glossary-popover"
        style="top: {popoverPos.top}px; left: {popoverPos.left}px"
        role="dialog"
        aria-label={shown?.term ?? ''}
      >
        <button
          type="button"
          class="glossary-popover-close"
          onclick={closeGlossaryPopover}
          aria-label={t($messages, 'close')}
        >
          <Icon name="close" size={14} />
        </button>
        <p class="kicker glossary-popover-kicker">{t($messages, 'navGlossary')}</p>
        <p class="glossary-popover-term">{shown?.term ?? ''}</p>
        <p class="glossary-popover-def">
          {shown?.definition ?? ''}
          {#if popoverLoading && !popoverEntry}<span class="glossary-popover-loading"
              >…</span
            >{/if}
        </p>
        {#if popoverFailed && !popoverEntry}
          <p class="glossary-popover-def muted">{t($messages, 'errorGeneric')}</p>
        {/if}
        <button type="button" class="glossary-popover-link" onclick={viewFullGlossaryEntry}>
          {t($messages, 'glossaryViewEntry')}<span aria-hidden="true"> →</span>
        </button>
      </div>
    {/if}
  </div>
{/if}

<style>
  /* Reading voice. Prose is the one place on the site set in a serif — the
     paper is machine-written, and this is where that machine is trying to be
     read rather than to report a measurement. */
  .md {
    color: var(--md-ink);
    font-family: var(--font-serif);
    font-weight: 400;
    font-size: var(--fs-prose);
    line-height: 1.7;
    overflow-wrap: anywhere;
  }
  @media (min-width: 520px) {
    .md {
      font-size: var(--fs-prose-lg);
      line-height: 1.72;
    }
  }

  .md :global(p) {
    margin: 0 0 1.15em;
  }
  .md :global(p:last-child) {
    margin-bottom: 0;
  }

  /* Lede — the dispatch's first breath, set apart from the body. Marked in
     JS above, since the opening paragraph may sit after lead art. */
  .md :global(> p.lede) {
    font-size: 1.22em;
    line-height: 1.48;
    color: var(--on-surface);
    margin: 0 0 1.35em;
    padding-bottom: 1.1em;
    border-bottom: 1px solid var(--border);
  }
  @media (min-width: 520px) {
    .md :global(> p.lede) {
      font-size: 1.28em;
      line-height: 1.5;
    }
  }

  .md :global(h1),
  .md :global(h2),
  .md :global(h3) {
    font-family: var(--font-display);
    font-weight: 700;
    color: var(--md-ink);
    line-height: 1.25;
  }
  .md :global(h1) {
    font-size: 26px;
    letter-spacing: -0.5px;
    margin: 12px 0 16px;
  }
  /* h2 carries a hairline above it so section breaks register while
     scanning — previously h2 and body were nearly the same weight. */
  .md :global(h2) {
    font-size: 23px;
    letter-spacing: -0.4px;
    margin: 38px 0 14px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .md :global(h2:first-child) {
    padding-top: 0;
    border-top: 0;
  }
  .md :global(h3) {
    font-size: 18px;
    letter-spacing: -0.3px;
    margin: 22px 0 10px;
  }
  @media (min-width: 520px) {
    .md :global(h1) {
      font-size: 30px;
      margin: 12px 0 16px;
    }
    .md :global(h2) {
      font-size: 27px;
      margin: 44px 0 14px;
    }
    .md :global(h3) {
      font-size: 20px;
      margin: 26px 0 10px;
    }
  }
  .md :global(h4) {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: var(--muted);
    margin: 20px 0 8px;
  }
  .md :global(h1:first-child),
  .md :global(h2:first-child),
  .md :global(h3:first-child),
  .md :global(h4:first-child) {
    margin-top: 0;
  }

  .md :global(strong) {
    font-weight: 700;
    color: var(--md-ink);
  }
  .md :global(em) {
    font-style: italic;
  }

  .md :global(a) {
    color: var(--accent);
    text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent);
    text-decoration-thickness: 1.4px;
    text-underline-offset: 2px;
  }
  .md :global(a:hover) {
    text-decoration-color: var(--accent);
  }

  /* Glossary auto-links read as "opens a definition", not "leaves the
     article" -- a dotted underline instead of the citation/source solid
     one, same accent ink so it still reads as a link. The native `title`
     attribute (set by glossary_linker.py) still gives a hover preview for
     free; click opens the richer in-page popover below. */
  .md :global(a.glossary-term) {
    text-decoration-style: dotted;
    text-decoration-thickness: 1.6px;
    text-underline-offset: 3px;
    cursor: pointer;
  }

  /* Pull quote: italic serif between hairlines — type, not a callout bar. */
  .md :global(blockquote) {
    margin: 32px 0;
    padding: 18px 0;
    border: 0;
    border-block: 1px solid var(--border);
    border-radius: 0;
    background: transparent;
    font-family: var(--font-serif);
    font-style: italic;
    font-size: 1.16em;
    line-height: 1.45;
    letter-spacing: -0.01em;
    color: var(--on-surface);
  }
  .md :global(blockquote p) {
    margin-bottom: 10px;
  }
  .md :global(blockquote p:last-child) {
    margin-bottom: 0;
  }
  @media (min-width: 520px) {
    .md :global(blockquote) {
      margin: 40px 0;
      padding: 22px 0;
      font-size: 1.2em;
    }
  }

  .md :global(ul),
  .md :global(ol) {
    margin: 0 0 18px;
    padding-inline-start: 1.55em;
  }
  .md :global(li) {
    margin-bottom: 10px;
  }
  .md :global(li > p) {
    margin-bottom: 8px;
  }
  .md :global(li > ul),
  .md :global(li > ol) {
    margin: 8px 0 0;
  }

  /* Sources / References — collapsed by default behind a native <details>
     disclosure (same mechanism the admin panels use for the same job:
     QueueTab's history, AnalyticsTab's ref-group), since a dozen-plus
     citations otherwise run the reader off the end of the article. The
     heading keeps its normal h2 rule (border-top hairline, size, margin)
     unchanged — it's just nested inside <summary> now — so only the
     disclosure affordance itself needs new rules here. */
  .md :global(.cite-summary) {
    cursor: pointer;
  }
  .md :global(.cite-summary)::marker {
    color: var(--muted);
    font-size: 0.7em;
  }
  .md :global(.cite-summary:hover h2),
  .md :global(.cite-summary:hover h3) {
    color: var(--accent);
  }
  .md :global(.cite-count) {
    font-family: var(--font-mono);
    font-size: 0.48em;
    font-weight: 600;
    letter-spacing: 0.3px;
    color: var(--muted);
    vertical-align: middle;
  }
  .md :global(details.cite-block[open] .cite-summary) {
    margin-bottom: 4px;
  }

  /* Sources / References — a citation ledger, not a bulleted URL dump. */
  .md :global(.cite-list) {
    list-style: none;
    padding: 0;
    margin: 4px 0 0;
  }
  .md :global(.cite-list li) {
    margin: 0;
    padding: 0;
    border-bottom: 1px solid var(--border);
  }
  .md :global(.cite-list li:first-child) {
    border-top: 1px solid var(--border);
  }
  .md :global(.cite-list a) {
    display: grid;
    grid-template-columns: minmax(9ch, 16ch) minmax(0, 1fr);
    gap: 8px 20px;
    align-items: baseline;
    padding: 11px 0;
    text-decoration: none;
    color: inherit;
  }
  .md :global(.cite-list a:hover) {
    text-decoration: none;
    color: var(--on-surface);
  }
  .md :global(.cite-host) {
    font-family: var(--font-mono);
    font-size: 0.68em;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .md :global(.cite-title) {
    font-family: var(--font-serif);
    font-size: 0.92em;
    color: var(--md-ink);
    min-width: 0;
  }
  .md :global(.cite-list a:hover .cite-host),
  .md :global(.cite-list a:hover .cite-title) {
    color: var(--accent);
  }
  @media (max-width: 519px) {
    .md :global(.cite-list a) {
      grid-template-columns: 1fr;
      gap: 2px;
      padding: 12px 0;
    }
  }

  .md :global(hr) {
    border: 0;
    border-top: 1px solid var(--border);
    margin: 28px 0;
  }

  .md :global(img) {
    display: block;
    max-width: 100%;
    max-height: 480px;
    width: auto;
    height: auto;
    margin: 12px auto 22px;
    object-fit: contain;
    object-position: center;
    border-radius: 0;
    background: var(--thumb-plate);
    border: 1px solid var(--border);
    padding: 8px;
    box-shadow: none;
  }

  @media (max-width: 519px) {
    .md :global(img) {
      max-height: 280px;
    }
  }

  .md :global(figure.plate) {
    margin: 28px 0;
  }
  .md :global(figure.plate img) {
    width: 100%;
    max-height: 420px;
    margin: 0;
    padding: 12px;
  }
  .md :global(figure.plate figcaption) {
    margin-top: 8px;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    line-height: 1.4;
  }
  @media (max-width: 519px) {
    .md :global(figure.plate) {
      margin: 22px 0;
    }
    .md :global(figure.plate img) {
      max-height: 260px;
      padding: 10px;
    }
  }

  .md :global(code) {
    font-family: var(--font-mono);
    font-size: 0.86em;
    padding: 0.12em 0.38em;
    border-radius: 5px;
    color: var(--primary);
    background: var(--callout);
  }
  :global([data-theme='dark']) .md :global(code) {
    color: var(--accent);
  }
  .md :global(pre) {
    overflow: auto;
    margin: 0 0 18px;
    padding: 16px;
    background: var(--callout);
    border: 1px solid var(--border);
    border-radius: 0;
    font-size: 0.88em;
    line-height: 1.55;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-x: contain;
  }
  .md :global(pre code) {
    padding: 0;
    background: transparent;
    color: inherit;
    font-size: inherit;
    border-radius: 0;
  }

  /* Data tables: smaller, tabular figures, and allowed to use the full
     column width rather than wrapping every cell to two lines. */
  .md :global(.table-frame) {
    position: relative;
    margin: 0 0 24px;
  }
  /* The wrapper is the scroll container; the table keeps table layout.
     display:block on the table used to fight this and clip mid-word. */
  .md :global(.table-scroll) {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-x: contain;
    /* Scroll-shadow cue: a wide table has no visible sign it scrolls, so a
       phone reader sees the last column cut off with no hint there's more
       to swipe to. Two edge-fixed radial gradients sit under two content-
       fixed opaque fades; a shadow only shows on the side that still has
       more to reveal. https://lea.verou.me/blog/2012/04/background-attachment-local/ */
    background:
      linear-gradient(to right, var(--surface) 30%, transparent),
      linear-gradient(to left, var(--surface) 30%, transparent) 100% 0,
      radial-gradient(farthest-side at 0 50%, rgba(0, 0, 0, 0.18), transparent),
      radial-gradient(farthest-side at 100% 50%, rgba(0, 0, 0, 0.18), transparent) 100% 0;
    background-repeat: no-repeat;
    background-color: var(--surface);
    background-size: 24px 100%, 24px 100%, 10px 100%, 10px 100%;
    background-attachment: local, local, scroll, scroll;
  }
  .md :global(.table-hint) {
    position: absolute;
    top: 8px;
    inset-inline-end: 4px;
    z-index: 1;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
    color: var(--accent);
    background: color-mix(in srgb, var(--surface) 92%, transparent);
    padding: 3px 8px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.2s ease;
  }
  .md :global(.table-frame.is-overflow .table-hint) {
    opacity: 1;
  }
  :global([dir='rtl']) .md :global(.table-hint) {
    transform: scaleX(-1);
  }
  .md :global(table) {
    /* Fill the measure when it fits; cell min-width below is what forces
       8-column pipeline tables to overflow the wrapper and scroll. */
    width: 100%;
    border-collapse: collapse;
    font-family: var(--font-mono);
    font-size: 0.84em;
    line-height: 1.55;
    font-variant-numeric: tabular-nums;
  }
  .md :global(thead) {
    border-bottom: 2px solid var(--on-surface);
  }
  .md :global(th) {
    text-align: start;
    font-size: 10.5px;
    font-weight: 800;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    padding: 8px 18px 8px 0;
    color: var(--muted);
    white-space: nowrap;
  }
  /* Cells wrap. `nowrap` kept every table on one line and made them read as
     a cramped strip; prose tables here are mostly short phrases, not figures. */
  .md :global(td) {
    padding: 11px 18px 11px 0;
    border-top: 1px solid var(--border);
    color: var(--md-ink);
    vertical-align: top;
    /* Parent .md uses overflow-wrap:anywhere, which was the mid-word clip
       on wide tables. Cells wrap at word boundaries only. */
    overflow-wrap: normal;
    word-break: normal;
  }
  .md :global(td:last-child),
  .md :global(th:last-child) {
    padding-inline-end: 12px;
  }
  /* A readability floor per column, on top of the content sizing above. */
  .md :global(th),
  .md :global(td) {
    min-width: 7.5ch;
    overflow-wrap: normal;
    word-break: normal;
  }
  /* Zebra fought the hairlines; rules alone separate rows more cleanly. */
  .md :global(tr:nth-child(even) td) {
    background: transparent;
  }

  .md :global(figure) {
    margin: 0 0 18px;
  }
  .md :global(figcaption) {
    margin-top: 8px;
    font-size: 0.85em;
    color: var(--muted);
    line-height: 1.45;
  }

  /* chart_data tool output — see chartRender.ts. Sits where a plain code
     block would otherwise land, styled like the rest of the site's data
     panels (mono figures, tabular-nums) rather than the serif prose voice. */
  .md :global(.chart-figure) {
    margin: 0 0 18px;
    padding: 16px 16px 12px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 0;
  }
  .md :global(.chart-title) {
    margin: 0 0 10px;
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.3px;
    color: var(--muted);
  }
  .md :global(.chart-svg) {
    display: block;
    width: 100%;
    height: auto;
  }
  .md :global(.chart-grid) {
    stroke: var(--border);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .md :global(.chart-baseline) {
    stroke: var(--on-surface);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .md :global(.chart-axis-label) {
    font-family: var(--font-mono);
    font-size: 10px;
    font-variant-numeric: tabular-nums;
    fill: var(--subtle);
  }
  .md :global(.chart-legend) {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 10px;
  }
  .md :global(.chart-legend-item) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--muted);
  }
  .md :global(.chart-legend-swatch) {
    width: 9px;
    height: 9px;
    border-radius: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .md :global(.table-hint) {
      transition: none;
    }
  }

  /* Anchors the glossary popover to the article flow instead of the
     viewport, so it scrolls with the text it's attached to. */
  .md-wrap {
    position: relative;
  }

  /* Same "floating card over prose" pattern as AnnotatedMarkdown's
     comment-popover (the site's one other inline-in-body popover), themed
     with the mono kicker + accent square used everywhere else a strip of
     machine-stamped metadata sits above hand-set type (masthead dateline,
     AppShell's nav popover-hint, this page's own .kicker headers). */
  .glossary-popover {
    position: absolute;
    z-index: 5;
    width: min(300px, 84vw);
    padding: 14px 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
    animation: glossary-pop-in 0.15s ease both;
  }
  @keyframes glossary-pop-in {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .glossary-popover {
      animation: none;
    }
  }
  .glossary-popover-close {
    position: absolute;
    top: 8px;
    inset-inline-end: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--muted);
  }
  .glossary-popover-close:hover {
    color: var(--accent);
  }
  .glossary-popover-kicker {
    display: flex;
    align-items: center;
    padding-inline-end: 24px;
  }
  .glossary-popover-kicker::before {
    content: '';
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-inline-end: 8px;
    background: var(--accent);
  }
  .glossary-popover-term {
    margin: 6px 0 0;
    font-family: var(--font-display);
    font-size: 1.02rem;
    font-weight: 700;
    color: var(--on-surface);
  }
  .glossary-popover-def {
    margin: 6px 0 0;
    font-family: var(--font-serif);
    font-size: 0.9rem;
    line-height: 1.5;
    color: var(--md-ink);
  }
  .glossary-popover-loading {
    color: var(--muted);
  }
  .glossary-popover-link {
    display: inline-flex;
    margin-top: 10px;
    padding: 0;
    border: 0;
    background: transparent;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: var(--accent);
  }
  .glossary-popover-link:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
</style>
