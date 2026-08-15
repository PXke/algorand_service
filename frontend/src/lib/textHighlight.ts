/**
 * Pure DOM utilities for anchoring a comment to a highlighted span of text
 * within a rendered Markdown container, and re-locating/highlighting it on a
 * later render.
 *
 * Uses a W3C TextQuoteSelector-style anchor (the exact quote plus a short
 * span of surrounding context) resolved against the container's CURRENT
 * flattened text at read time -- never raw offsets into the markdown
 * source, since the renderer's own transforms (lede tagging, chart blocks,
 * image filtering) don't preserve a stable source-to-DOM offset mapping.
 *
 * No Svelte import -- independently testable.
 */

export interface TextQuoteAnchor {
  quote: string
  prefix: string
  suffix: string
}

const CONTEXT_CHARS = 32

interface TextNodeSpan {
  node: Text
  start: number
  end: number
}

function buildTextIndex(container: HTMLElement): { spans: TextNodeSpan[]; flat: string } {
  const spans: TextNodeSpan[] = []
  let flat = ''
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  let node = walker.nextNode()
  while (node) {
    const text = node as Text
    const start = flat.length
    flat += text.data
    spans.push({ node: text, start, end: flat.length })
    node = walker.nextNode()
  }
  return { spans, flat }
}

function findFirstTextNode(node: Node): Text | null {
  if (node.nodeType === Node.TEXT_NODE) return node as Text
  for (const child of Array.from(node.childNodes)) {
    const found = findFirstTextNode(child)
    if (found) return found
  }
  return null
}

function findLastTextNode(node: Node): Text | null {
  if (node.nodeType === Node.TEXT_NODE) return node as Text
  const children = Array.from(node.childNodes)
  for (let i = children.length - 1; i >= 0; i--) {
    const found = findLastTextNode(children[i])
    if (found) return found
  }
  return null
}

function pointToFlatOffset(spans: TextNodeSpan[], node: Node, offset: number): number | null {
  if (node.nodeType === Node.TEXT_NODE) {
    const span = spans.find((s) => s.node === node)
    if (!span) return null
    return span.start + Math.min(offset, span.node.data.length)
  }
  // Element boundary: offset is a child index, not a character offset.
  const children = node.childNodes
  if (offset < children.length) {
    const firstText = findFirstTextNode(children[offset])
    if (firstText) {
      const span = spans.find((s) => s.node === firstText)
      if (span) return span.start
    }
  }
  // Past the last child (or no text node forward from here) -- use the end
  // of the nearest preceding text descendant.
  for (let i = Math.min(offset, children.length) - 1; i >= 0; i--) {
    const lastText = findLastTextNode(children[i])
    if (lastText) {
      const span = spans.find((s) => s.node === lastText)
      if (span) return span.end
    }
  }
  return null
}

function flatOffsetToPoint(spans: TextNodeSpan[], offset: number): { node: Text; offset: number } | null {
  for (const span of spans) {
    if (offset >= span.start && offset <= span.end) {
      return { node: span.node, offset: offset - span.start }
    }
  }
  return null
}

/** How many trailing/leading characters of `expected` match `actual`, stopping at the first mismatch nearest the quote. */
function contextMatchScore(actual: string, expected: string, fromEnd: boolean): number {
  let score = 0
  const len = Math.min(actual.length, expected.length)
  for (let i = 0; i < len; i++) {
    const a = fromEnd ? actual[actual.length - 1 - i] : actual[i]
    const b = fromEnd ? expected[expected.length - 1 - i] : expected[i]
    if (a === b) score++
    else break
  }
  return score
}

/** Capture a TextQuoteAnchor for the user's current selection Range within `container`. Returns null for a collapsed/empty/unresolvable selection. */
export function captureSelectionAnchor(container: HTMLElement, range: Range): TextQuoteAnchor | null {
  const { spans, flat } = buildTextIndex(container)
  const start = pointToFlatOffset(spans, range.startContainer, range.startOffset)
  const end = pointToFlatOffset(spans, range.endContainer, range.endOffset)
  if (start === null || end === null || end <= start) return null
  const quote = flat.slice(start, end)
  if (!quote.trim()) return null
  const prefix = flat.slice(Math.max(0, start - CONTEXT_CHARS), start)
  const suffix = flat.slice(end, Math.min(flat.length, end + CONTEXT_CHARS))
  return { quote, prefix, suffix }
}

/** Re-locate a stored anchor within the CURRENT rendered `container`. Returns null when the quote no longer appears (article edited since the comment was made) -- callers should treat that as "orphaned", not an error. */
export function resolveAnchorToRange(container: HTMLElement, anchor: TextQuoteAnchor): Range | null {
  if (!anchor.quote) return null
  const { spans, flat } = buildTextIndex(container)

  const occurrences: number[] = []
  let idx = flat.indexOf(anchor.quote)
  while (idx !== -1) {
    occurrences.push(idx)
    idx = flat.indexOf(anchor.quote, idx + 1)
  }
  if (occurrences.length === 0) return null

  let bestStart = occurrences[0]
  if (occurrences.length > 1) {
    let bestScore = -1
    for (const candidate of occurrences) {
      const candidateEnd = candidate + anchor.quote.length
      const actualPrefix = flat.slice(Math.max(0, candidate - anchor.prefix.length), candidate)
      const actualSuffix = flat.slice(candidateEnd, candidateEnd + anchor.suffix.length)
      const score =
        contextMatchScore(actualPrefix, anchor.prefix, true) +
        contextMatchScore(actualSuffix, anchor.suffix, false)
      if (score > bestScore) {
        bestScore = score
        bestStart = candidate
      }
    }
  }

  const start = bestStart
  const end = start + anchor.quote.length
  const startPoint = flatOffsetToPoint(spans, start)
  const endPoint = flatOffsetToPoint(spans, end)
  if (!startPoint || !endPoint) return null

  const range = document.createRange()
  range.setStart(startPoint.node, startPoint.offset)
  range.setEnd(endPoint.node, endPoint.offset)
  return range
}

/**
 * Wrap every text node intersecting `range` in its own `<mark>` (carrying
 * `attrs`), splitting boundary text nodes as needed. Avoids
 * `Range.surroundContents`, which throws when a range doesn't nest cleanly
 * within one parent (a highlight spanning e.g. `<em>` + plain text will) --
 * a highlight crossing inline element boundaries becomes N marks instead.
 */
export function wrapRangeWithMark(range: Range, attrs: Record<string, string>): HTMLElement[] {
  const ancestor =
    range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? (range.commonAncestorContainer as HTMLElement)
      : range.commonAncestorContainer.parentElement
  if (!ancestor) return []

  const { spans } = buildTextIndex(ancestor)
  const intersecting = spans.filter((s) => range.intersectsNode(s.node))
  const marks: HTMLElement[] = []

  for (const span of intersecting) {
    const node = span.node
    const start = node === range.startContainer ? range.startOffset : 0
    const end = node === range.endContainer ? range.endOffset : node.data.length
    if (start >= end) continue

    let target: Text = node
    if (end < target.data.length) {
      target.splitText(end)
    }
    if (start > 0) {
      target = target.splitText(start)
    }

    const mark = document.createElement('mark')
    for (const [key, value] of Object.entries(attrs)) mark.setAttribute(key, value)
    target.parentNode?.insertBefore(mark, target)
    mark.appendChild(target)
    marks.push(mark)
  }

  return marks
}

/** Unwrap every `<mark>` previously inserted by `wrapRangeWithMark` (default selector) and merge text nodes back, so the next resolve pass starts from a clean container. */
export function clearMarks(container: HTMLElement, selector = 'mark[data-comment-id]'): void {
  const marks = container.querySelectorAll(selector)
  marks.forEach((mark) => {
    const parent = mark.parentNode
    if (!parent) return
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
    parent.removeChild(mark)
  })
  container.normalize()
}
