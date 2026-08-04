export function arrayItemsOf<T>(body: Record<string, unknown>): T[] {
  const items = body.items
  if (!Array.isArray(items)) return []
  return items.filter((x): x is T => !!x && typeof x === 'object') as T[]
}
