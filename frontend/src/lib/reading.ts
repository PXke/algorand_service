/** Estimate article reading time from markdown/plain body. */
export function readingMinutes(body: string | null | undefined): number {
  const text = (body || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]+`/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, ' ')
    .replace(/\[[^\]]*\]\([^)]+\)/g, ' ')
    .replace(/[#>*_~|-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return 1
  const words = text.split(' ').filter(Boolean).length
  return Math.min(999, Math.max(1, Math.ceil(words / 220)))
}
