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
  .md {
    color: var(--md-ink);
    font-family: var(--font-sans);
    font-weight: 400;
    font-size: 17.5px;
    line-height: 1.72;
    overflow-wrap: anywhere;
  }
  @media (min-width: 520px) {
    .md {
      font-size: 19px;
      line-height: 1.8;
    }
  }

  .md :global(p) {
    margin: 0 0 18px;
  }
  .md :global(p:last-child) {
    margin-bottom: 0;
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
    font-size: 30px;
    letter-spacing: -0.5px;
    margin: 12px 0 16px;
  }
  .md :global(h2) {
    font-size: 24px;
    letter-spacing: -0.4px;
    margin: 34px 0 12px;
  }
  .md :global(h3) {
    font-size: 20px;
    letter-spacing: -0.3px;
    margin: 26px 0 10px;
  }
  .md :global(h4) {
    font-family: var(--font-sans);
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

  .md :global(blockquote) {
    margin: 0 0 18px;
    padding: 10px 14px 10px 18px;
    border-inline-start: 3px solid var(--accent);
    border-radius: 0 10px 10px 0;
    background: color-mix(in srgb, var(--accent) 6%, transparent);
    font-style: italic;
    font-size: 17px;
    line-height: 1.7;
    color: var(--muted);
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

  .md :global(code) {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
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
  }
  .md :global(pre code) {
    padding: 0;
    background: transparent;
    color: inherit;
    font-size: inherit;
    border-radius: 0;
  }

  .md :global(table) {
    width: 100%;
    border-collapse: collapse;
    margin: 0 0 22px;
    font-size: 0.92em;
    line-height: 1.55;
    overflow-x: auto;
    display: block;
  }
  .md :global(thead) {
    border-bottom: 1px solid var(--border);
  }
  .md :global(th) {
    text-align: start;
    font-weight: 700;
    padding: 10px 12px;
    color: var(--on-surface);
    white-space: nowrap;
  }
  .md :global(td) {
    padding: 10px 12px;
    border-top: 1px solid var(--border);
    color: var(--md-ink);
    vertical-align: top;
  }
  .md :global(tr:nth-child(even) td) {
    background: color-mix(in srgb, var(--callout) 55%, transparent);
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
