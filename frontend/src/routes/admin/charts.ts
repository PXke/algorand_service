/** Shared SVG chart geometry for admin analytics (no chart library). */

export const CHART_PALETTE = [
  'var(--primary)',
  '#2dd4bf',
  '#fb923c',
  '#c084fc',
  '#60a5fa',
  '#f472b6',
  '#4ade80',
  '#fbbf24',
  '#818cf8',
  '#a8a29e',
]

export type BarPoint = { label: string; value: number }
export type StackSeries = { key: string; color: string }
export type StackPoint = { label: string; values: Record<string, number> }

export function shortDay(day: string, year = new Date().getFullYear()): string {
  return day.replace(`${year}-`, '')
}

export function deltaPct(current: number, previous: number): number | null {
  if (previous <= 0) return null
  return ((current - previous) / previous) * 100
}

export function asRows(raw: unknown): Array<Record<string, unknown>> {
  return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : []
}

export function asMap(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === 'object' && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : {}
}

export function num(v: unknown, fallback = 0): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

export function str(v: unknown, fallback = ''): string {
  return v == null ? fallback : String(v)
}

export type SimpleBarLayout = {
  w: number
  h: number
  padL: number
  padT: number
  plotH: number
  bars: Array<{ x: number; y: number; w: number; h: number; v: number; label: string }>
  ticks: Array<{ y: number; val: number }>
  showEvery: number
}

export function layoutSimpleBars(
  points: BarPoint[],
  opts: { w?: number; h?: number } = {},
): SimpleBarLayout | null {
  if (points.length === 0) return null
  const w = opts.w ?? 640
  const h = opts.h ?? 200
  const padL = 36
  const padR = 8
  const padT = 12
  const padB = 28
  const plotW = w - padL - padR
  const plotH = h - padT - padB
  const max = Math.max(1, ...points.map((p) => p.value))
  const gap = points.length > 20 ? 2 : 4
  const barW = Math.max(3, (plotW - gap * (points.length - 1)) / points.length)
  const bars = points.map((p, i) => {
    const bh = (p.value / (max * 1.15)) * plotH
    return {
      x: padL + i * (barW + gap),
      y: padT + plotH - bh,
      w: barW,
      h: Math.max(p.value > 0 ? 2 : 0, bh),
      v: p.value,
      label: p.label,
    }
  })
  const ticks = [0, 0.5, 1].map((t) => {
    const val = Math.round(max * t)
    return { y: padT + plotH - (val / (max * 1.15)) * plotH, val }
  })
  return {
    w,
    h,
    padL,
    padT,
    plotH,
    bars,
    ticks,
    showEvery: points.length > 7 ? 2 : 1,
  }
}

export type StackBarLayout = {
  w: number
  h: number
  padL: number
  padT: number
  plotH: number
  series: StackSeries[]
  columns: Array<{
    x: number
    w: number
    label: string
    segments: Array<{ y: number; h: number; color: string; v: number; key: string }>
  }>
  ticks: Array<{ y: number; val: number }>
  showEvery: number
}

export function layoutStackedBars(
  points: StackPoint[],
  series: StackSeries[],
  opts: { w?: number; h?: number } = {},
): StackBarLayout | null {
  if (points.length === 0 || series.length === 0) return null
  const w = opts.w ?? 640
  const h = opts.h ?? 200
  const padL = 36
  const padR = 8
  const padT = 12
  const padB = 28
  const plotW = w - padL - padR
  const plotH = h - padT - padB
  const totals = points.map((p) => series.reduce((s, ser) => s + (p.values[ser.key] ?? 0), 0))
  const max = Math.max(1, ...totals)
  const gap = points.length > 20 ? 2 : 4
  const barW = Math.max(4, (plotW - gap * (points.length - 1)) / points.length)
  const columns = points.map((p, i) => {
    let from = 0
    const segments = series.map((ser) => {
      const v = p.values[ser.key] ?? 0
      const segH = (v / (max * 1.15)) * plotH
      const y = padT + plotH - ((from + v) / (max * 1.15)) * plotH
      from += v
      return { y, h: Math.max(v > 0 ? 1 : 0, segH), color: ser.color, v, key: ser.key }
    })
    return {
      x: padL + i * (barW + gap),
      w: barW,
      label: p.label,
      segments,
    }
  })
  const ticks = [0, 0.5, 1].map((t) => {
    const val = Math.round(max * t)
    return { y: padT + plotH - (val / (max * 1.15)) * plotH, val }
  })
  return {
    w,
    h,
    padL,
    padT,
    plotH,
    series,
    columns,
    ticks,
    showEvery: points.length > 7 ? 2 : 1,
  }
}

export type DonutSlice = { label: string; value: number; color: string }

export function donutSlices(
  rows: Array<Record<string, unknown>>,
  keyName: string,
): { slices: DonutSlice[]; total: number } {
  const slices: DonutSlice[] = []
  let total = 0
  for (let i = 0; i < rows.length; i++) {
    const v = num(rows[i]?.views)
    total += v
    slices.push({
      label: str(rows[i]?.[keyName], '—'),
      value: v,
      color: CHART_PALETTE[i % CHART_PALETTE.length]!,
    })
  }
  return { slices, total }
}

/** SVG arc path for a donut segment. */
export function donutArc(
  cx: number,
  cy: number,
  rOuter: number,
  rInner: number,
  startAngle: number,
  endAngle: number,
): string {
  const polar = (r: number, a: number) => {
    const rad = ((a - 90) * Math.PI) / 180
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
  }
  const large = endAngle - startAngle > 180 ? 1 : 0
  const s = polar(rOuter, startAngle)
  const e = polar(rOuter, endAngle)
  const s2 = polar(rInner, endAngle)
  const e2 = polar(rInner, startAngle)
  return [
    `M ${s.x} ${s.y}`,
    `A ${rOuter} ${rOuter} 0 ${large} 1 ${e.x} ${e.y}`,
    `L ${s2.x} ${s2.y}`,
    `A ${rInner} ${rInner} 0 ${large} 0 ${e2.x} ${e2.y}`,
    'Z',
  ].join(' ')
}

export function sparklinePath(
  daily: Array<Record<string, unknown>>,
  w = 90,
  h = 34,
): { line: string; fill: string } | null {
  if (daily.length < 2) return null
  const vals = daily.map((d) => num(d.views))
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const span = Math.abs(max - min) < 1e-9 ? 1 : max - min
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w
    const y = h * 0.92 - ((v - min) / span) * h * 0.84
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const line = pts.join(' ')
  const first = pts[0]!
  const last = pts[pts.length - 1]!
  return {
    line,
    fill: `${first} ${line} ${last.split(',')[0]},${h} 0,${h}`,
  }
}

export function flagEmoji(cc: string): string {
  const up = cc.toUpperCase()
  if (up.length !== 2) return '🏳️'
  const a = up.charCodeAt(0)
  const b = up.charCodeAt(1)
  if (a < 65 || a > 90 || b < 65 || b > 90) return '🏳️'
  return String.fromCodePoint(0x1f1e6 + (a - 65), 0x1f1e6 + (b - 65))
}
