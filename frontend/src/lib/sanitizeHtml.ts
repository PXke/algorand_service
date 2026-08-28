/**
 * Sanitizes HTML produced by `marked.parse()` (article bodies, glossary
 * definitions, shared/annotated views) before it is ever handed to
 * `{@html}`. The source markdown is writer/LLM output and crawled-page
 * content -- not something we can treat as trusted -- so every render goes
 * through this allowlist rather than relying on marked's own escaping
 * (marked does not sanitize raw HTML that slips through inline in the
 * source markdown).
 *
 * Kept deliberately permissive on *structural* tags/attributes (figure,
 * details, svg, table wrappers, chart legend spans, the glossary linker's
 * data-glossary-slug) because Markdown.svelte's custom renderers and
 * lib/chartRender.ts legitimately emit them -- the security boundary here
 * is the URL scheme (no javascript:/data:) and event-handler attributes
 * (no on*), not the tag/class vocabulary.
 */
import DOMPurify from 'dompurify'

// Only http(s), mailto, and scheme-less (relative/anchor/protocol-relative)
// URLs pass. This intentionally excludes javascript: and data: -- the two
// schemes an attacker can turn into script execution or a same-origin-ish
// document via href/src.
const ALLOWED_URI_REGEXP = /^(?:(?:https?|mailto):|[^a-z]|[a-z\d.+-]*(?:[^a-z\d+.:-]|$))/i

const URI_ATTR_NAMES = new Set(['href', 'src', 'xlink:href'])

// A scheme can hide behind ASCII whitespace/control chars (e.g.
// "jav\tascript:") that browsers ignore when resolving it; strip them
// before testing so that trick can't slip past the regex above.
const CONTROL_CHARS_RE = /[\x00-\x20]+/g

let hookInstalled = false

/** DOMPurify's own `ALLOWED_URI_REGEXP` config still special-cases `data:`
 *  as always-safe for img/audio/video/source/image/track (`DATA_URI_TAGS`,
 *  only additive via config, never shrinkable). A hook is the only way to
 *  actually forbid data: URIs everywhere, which this component's XSS gate
 *  requires. */
function installUriGuardHook(): void {
  if (hookInstalled) return
  DOMPurify.addHook('uponSanitizeAttribute', (_node, data) => {
    if (!URI_ATTR_NAMES.has(data.attrName)) return
    const value = (data.attrValue || '').replace(CONTROL_CHARS_RE, '')
    if (!ALLOWED_URI_REGEXP.test(value)) {
      data.keepAttr = false
    }
  })
  hookInstalled = true
}

/** Sanitize a trusted-shape-but-untrusted-content HTML string for `{@html}`. */
export function sanitizeArticleHtml(html: string): string {
  if (!html) return ''
  installUriGuardHook()
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true, svg: true },
    ALLOWED_URI_REGEXP,
    ADD_ATTR: ['target', 'data-glossary-slug'],
    FORBID_TAGS: [
      'style',
      'form',
      'input',
      'button',
      'select',
      'textarea',
      'iframe',
      'object',
      'embed',
      'base',
      'link',
      'meta',
    ],
  })
}
