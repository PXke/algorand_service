/**
 * Renders a `chart_data` tool spec (```chart fenced JSON in an article body)
 * to a static SVG+HTML string for injection via Markdown.svelte's `{@html}`.
 *
 * A real mounted Svelte component isn't an option here — marked's renderer
 * produces one big HTML string, not a tree of live components — so this is
 * pure string templating instead. Fine for a static bar/line chart (no
 * interactivity needed); colors come from CSS custom properties so it
 * follows the page's live light/dark theme with no JS-side theme handling.
 */

const PALETTE_VARS = [
  'var(--accent)',
  'var(--gain)',
  'var(--loss)',
  'color-mix(in srgb, var(--accent) 55%, var(--muted))',
  'color-mix(in srgb, var(--gain) 55%, var(--loss))',
] as const

type ChartSeries = { name?: string; y: number[] }
type ChartSpec = { type: string; title?: string; x: string[]; series: ChartSeries[] }

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Compact axis/value label: integers bare, decimals to 2 places, no trailing zeros. */
function formatValue(v: number): string {
  if (!Number.isFinite(v)) return '0'
  if (Number.isInteger(v)) return String(v)
  return v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}

function parseChartSpec(raw: string): ChartSpec | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object') return null
  const obj = parsed as Record<string, unknown>
  const type = String(obj.type ?? '').toLowerCase()
  if (type !== 'bar' && type !== 'line') return null
  if (!Array.isArray(obj.x) || obj.x.length === 0) return null
  if (!Array.isArray(obj.series) || obj.series.length === 0) return null
  const x = obj.x.map((v) => String(v))
  const series: ChartSeries[] = []
  for (const s of obj.series) {
    if (!s || typeof s !== 'object') return null
    const rec = s as Record<string, unknown>
    if (!Array.isArray(rec.y) || rec.y.length !== x.length) return null
    const y = rec.y.map((n) => Number(n))
    if (y.some((n) => !Number.isFinite(n))) return null
    series.push({ name: rec.name != null ? String(rec.name) : undefined, y })
  }
  return { type, title: obj.title != null ? String(obj.title) : undefined, x, series }
}

const W = 640
const H = 320
const PAD_LEFT = 52
const PAD_RIGHT = 14
const PAD_TOP = 14
// Room for x-axis labels, rotated when there are many categories.
const PAD_BOTTOM_SHORT = 34
const PAD_BOTTOM_ROTATED = 64
const TICKS = 4

function niceMax(max: number): number {
  if (max <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(max))
  const normalized = max / magnitude
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return step * magnitude
}

function renderSvg(spec: ChartSpec): string {
  const allValues = spec.series.flatMap((s) => s.y)
  const dataMin = Math.min(0, ...allValues)
  const dataMax = Math.max(...allValues, 0)
  const yMax = niceMax(dataMax) || 1
  const yMin = dataMin < 0 ? -niceMax(-dataMin) : 0
  const rotateLabels = spec.x.length > 6
  const padBottom = rotateLabels ? PAD_BOTTOM_ROTATED : PAD_BOTTOM_SHORT
  const plotW = W - PAD_LEFT - PAD_RIGHT
  const plotH = H - PAD_TOP - padBottom
  const yToPx = (v: number) => PAD_TOP + plotH - ((v - yMin) / (yMax - yMin)) * plotH
  const zeroY = yToPx(0)

  const gridLines: string[] = []
  for (let i = 0; i <= TICKS; i++) {
    const v = yMin + ((yMax - yMin) * i) / TICKS
    const py = yToPx(v)
    gridLines.push(
      `<line x1="${PAD_LEFT}" y1="${py.toFixed(1)}" x2="${W - PAD_RIGHT}" y2="${py.toFixed(1)}" class="chart-grid" />`,
      `<text x="${PAD_LEFT - 8}" y="${py.toFixed(1)}" class="chart-axis-label" text-anchor="end" dominant-baseline="middle">${escapeHtml(formatValue(v))}</text>`,
    )
  }

  const n = spec.x.length
  const xLabels = spec.x
    .map((label, i) => {
      const cx = PAD_LEFT + ((i + 0.5) / n) * plotW
      const baseY = H - padBottom + 16
      const short = label.length > 14 ? `${label.slice(0, 13)}…` : label
      const transform = rotateLabels ? ` transform="rotate(-35 ${cx.toFixed(1)} ${baseY})"` : ''
      const anchor = rotateLabels ? 'end' : 'middle'
      return `<text x="${cx.toFixed(1)}" y="${baseY}" class="chart-axis-label" text-anchor="${anchor}"${transform}>${escapeHtml(short)}</text>`
    })
    .join('')

  let seriesMarkup = ''
  if (spec.type === 'bar') {
    const groupW = plotW / n
    const barGap = groupW * 0.18
    const barW = (groupW - barGap * 2) / spec.series.length
    seriesMarkup = spec.series
      .map((s, si) => {
        const color = PALETTE_VARS[si % PALETTE_VARS.length]
        return s.y
          .map((v, i) => {
            const groupX = PAD_LEFT + i * groupW + barGap
            const bx = groupX + si * barW
            const barY = yToPx(v)
            const top = Math.min(zeroY, barY)
            const height = Math.max(1, Math.abs(zeroY - barY))
            return `<rect x="${bx.toFixed(1)}" y="${top.toFixed(1)}" width="${Math.max(1, barW).toFixed(1)}" height="${height.toFixed(1)}" style="fill:${color}" rx="2"><title>${escapeHtml(s.name ?? '')} ${escapeHtml(spec.x[i])}: ${escapeHtml(formatValue(v))}</title></rect>`
          })
          .join('')
      })
      .join('')
  } else {
    seriesMarkup = spec.series
      .map((s, si) => {
        const color = PALETTE_VARS[si % PALETTE_VARS.length]
        const pts = s.y.map((v, i) => {
          const px = PAD_LEFT + ((i + 0.5) / n) * plotW
          const py = yToPx(v)
          return { px, py, v }
        })
        const path = pts
          .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.px.toFixed(1)} ${p.py.toFixed(1)}`)
          .join(' ')
        const dots = pts
          .map(
            (p, i) =>
              `<circle cx="${p.px.toFixed(1)}" cy="${p.py.toFixed(1)}" r="3" style="fill:${color}"><title>${escapeHtml(s.name ?? '')} ${escapeHtml(spec.x[i])}: ${escapeHtml(formatValue(p.v))}</title></circle>`,
          )
          .join('')
        return `<path d="${path}" fill="none" style="stroke:${color}" stroke-width="2.25" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />${dots}`
      })
      .join('')
  }

  const hasNamedSeries = spec.series.some((s) => s.name)
  const legend =
    hasNamedSeries && spec.series.length > 1
      ? `<div class="chart-legend">${spec.series
          .map((s, i) => {
            const color = PALETTE_VARS[i % PALETTE_VARS.length]
            return `<span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${color}"></span>${escapeHtml(s.name ?? '')}</span>`
          })
          .join('')}</div>`
      : ''

  const titleMarkup = spec.title
    ? `<figcaption class="chart-title">${escapeHtml(spec.title)}</figcaption>`
    : ''

  return (
    `<figure class="chart-figure">${titleMarkup}` +
    `<svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img" aria-label="${escapeHtml(spec.title ?? 'chart')}">` +
    `<line x1="${PAD_LEFT}" y1="${zeroY.toFixed(1)}" x2="${W - PAD_RIGHT}" y2="${zeroY.toFixed(1)}" class="chart-baseline" />` +
    gridLines.join('') +
    seriesMarkup +
    xLabels +
    `</svg>${legend}</figure>`
  )
}

/** Render a ```chart fenced block's raw text to HTML, or null if it isn't a valid chart spec (caller should fall back to default code rendering). */
export function renderChartHtml(raw: string): string | null {
  const spec = parseChartSpec(raw)
  if (!spec) return null
  try {
    return renderSvg(spec)
  } catch {
    return null
  }
}
