/** Stagger delay for list entrance animations (ms). */
export function staggerMs(index: number, step = 42, cap = 380): number {
  return Math.min(index * step, cap)
}

/** Map article ids → stagger index for rows at/after `startIndex`. */
export function markFeedEnter(
  items: readonly { article_id: string }[],
  startIndex = 0,
): Map<string, number> {
  const map = new Map<string, number>()
  for (let i = startIndex; i < items.length; i++) {
    map.set(items[i].article_id, i - startIndex)
  }
  return map
}

export function markFeedEnterAll(items: readonly { article_id: string }[]): Map<string, number> {
  return markFeedEnter(items, 0)
}

export function feedEnterIndex(
  map: Map<string, number>,
  articleId: string,
): number | undefined {
  return map.get(articleId)
}
