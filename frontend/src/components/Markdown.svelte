<script lang="ts">
  import { marked, Renderer } from 'marked'
  import { looksLikeLogoUrl, proxiedImageUrl } from '../lib/images'

  let { source = '' }: { source?: string } = $props()

  /** Drop favicon/logo-only image lines the pipeline sometimes injects as lead art. */
  function cleanSource(raw: string): string {
    return raw.replace(
      /^!\[[^\]]*\]\((\S+?)(?:\s+"[^"]*")?\)\s*$/gm,
      (full, url: string) => (looksLikeLogoUrl(url) ? '' : full),
    )
  }

  const html = $derived.by(() => {
    const cleaned = cleanSource(source || '').trim()
    if (!cleaned) return ''

    const renderer = new Renderer()
    const baseImage = renderer.image.bind(renderer)
    renderer.image = ({ href, title, text }) => {
      const url = href ?? ''
      if (!url || looksLikeLogoUrl(url)) return ''
      return baseImage({ href: proxiedImageUrl(url), title, text })
    }
    /* Mark the opening paragraph at parse time. Doing it after render meant
       the lede's font-size landed a frame late — a layout shift on every
       article. Image-only and empty paragraphs are skipped, so a body that
       opens with lead art still tags the first real prose. */
    let ledeSeen = false
    const baseParagraph = renderer.paragraph.bind(renderer)
    renderer.paragraph = (token) => {
      const out = baseParagraph(token)
      if (ledeSeen) return out
      const bare = out.replace(/<[^>]*>/g, '').trim()
      if (!bare || /<img\b/i.test(out)) return out
      ledeSeen = true
      return out.replace('<p>', '<p class="lede">')
    }

    /* Wrap tables so a wide one scrolls instead of being squeezed. Pipeline
       tables run to 8 columns; forced into the reading measure that left ~45px
       a column on a phone, which is not a table anyone can read. The wrapper
       scrolls, and the cell min-width below is what decides when. */
    const baseTable = renderer.table.bind(renderer)
    renderer.table = (token) => `<div class="table-scroll">${baseTable(token)}</div>`

    const baseLink = renderer.link.bind(renderer)
    renderer.link = ({ href, title, tokens }) => {
      const out = baseLink({ href, title, tokens })
      // Open external http(s) links in a new tab.
      if (href && /^https?:\/\//i.test(href)) {
        return out.replace('<a ', '<a target="_blank" rel="noopener noreferrer" ')
      }
      return out
    }

    return marked.parse(cleaned, {
      async: false,
      gfm: true,
      breaks: false,
      renderer,
    }) as string
  })

</script>

{#if html}
  <div class="md">
    {@html html}
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
    font-size: 18.5px;
    line-height: 1.68;
    overflow-wrap: anywhere;
  }
  @media (min-width: 520px) {
    .md {
      font-size: 20px;
      line-height: 1.72;
    }
  }

  .md :global(p) {
    margin: 0 0 18px;
  }
  .md :global(p:last-child) {
    margin-bottom: 0;
  }

  /* Lede — marked in JS above, since the opening paragraph may sit after a
     lead image, a heading, or both. */
  .md :global(> p.lede) {
    font-size: 1.1em;
    line-height: 1.62;
    color: var(--on-surface);
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

  /* Pull quote, not a tinted callout box: display serif at reading scale,
     set off by a heavy tone rule and air. Quotes are the one place the body
     column is allowed to change voice. */
  .md :global(blockquote) {
    margin: 26px 0;
    padding: 2px 0 2px 22px;
    border-inline-start: 4px solid var(--tone, var(--accent));
    border-radius: 0;
    background: transparent;
    font-family: var(--font-display);
    font-stretch: 94%;
    font-style: normal;
    font-size: 1.16em;
    line-height: 1.34;
    letter-spacing: -0.2px;
    color: var(--on-surface);
  }
  @media (min-width: 700px) {
    .md :global(blockquote) {
      margin-inline: -22px 0;
    }
  }
  .md :global(blockquote p) {
    margin-bottom: 10px;
  }
  .md :global(blockquote p:last-child) {
    margin-bottom: 0;
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

  .md :global(hr) {
    border: 0;
    border-top: 1px solid var(--border);
    margin: 28px 0;
  }

  .md :global(img) {
    display: block;
    /* Fit within the column without cropping — cover + forced width was
       slicing portraits and wide screenshots badly. */
    max-width: 100%;
    max-height: 480px;
    width: auto;
    height: auto;
    margin: 8px auto 18px;
    object-fit: contain;
    object-position: center;
    border-radius: 12px;
    background: var(--callout);
  }

  /* Lead art: the first image gets to breathe past the reading measure
     instead of sitting letterboxed inside it. */
  @media (min-width: 900px) {
    .md :global(> p:first-child > img:only-child),
    .md :global(> p:nth-child(2) > img:only-child),
    .md :global(> img:first-child) {
      width: calc(100% + 140px);
      max-width: calc(100% + 140px);
      margin-inline: -70px;
      max-height: 420px;
    }
  }
  @media (max-width: 519px) {
    .md :global(img) {
      max-height: 280px;
      border-radius: 8px;
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
    border-radius: 10px;
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
  /* The wrapper is the scroll container; the table keeps its own layout. */
  .md :global(.table-scroll) {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-x: contain;
    margin: 0 0 24px;
  }
  .md :global(table) {
    /* max-content + min-width:100%: the table sizes to its content so columns
       are never squeezed, but still fills the measure when it is narrow, so
       short tables stay flush with the prose. Cell min-width alone could not do
       this — an auto-layout table honours the cells' minimums only up to its
       own width:100%, so an 8-column table still collapsed to 45px columns. */
    width: max-content;
    min-width: 100%;
    border-collapse: collapse;
    font-family: var(--font-mono);
    font-size: 0.84em;
    line-height: 1.55;
    font-variant-numeric: tabular-nums;
    overflow-x: auto;
    display: block;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-x: contain;
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
  }
  .md :global(td:last-child),
  .md :global(th:last-child) {
    padding-inline-end: 0;
  }
  /* A readability floor per column, on top of the content sizing above. */
  .md :global(th),
  .md :global(td) {
    min-width: 7.5ch;
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
</style>
