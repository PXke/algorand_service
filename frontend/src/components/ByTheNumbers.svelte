<script lang="ts">
  import { messages, t, localeTag, activeLocale } from '../lib/i18n'

  let {
    price,
    history = [],
  }: {
    price: Record<string, unknown>
    history?: Array<{ epoch: number; price: number }>
  } = $props()

  const priceUsd = $derived(Number(price.price_usd ?? 0))
  const change = $derived(
    price.change_24h_pct == null ? null : Number(price.change_24h_pct),
  )
  const marketCap = $derived(
    price.market_cap_usd == null ? null : Number(price.market_cap_usd),
  )
  const volume = $derived(
    price.volume_24h_usd == null ? null : Number(price.volume_24h_usd),
  )
  const up = $derived((change ?? 0) >= 0)

  function compactUsd(value: number): string {
    if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
    if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
    if (value >= 1e3) return `$${(value / 1e3).toFixed(1)}K`
    return `$${value.toFixed(0)}`
  }

  const figures = $derived(
    [
      marketCap != null
        ? { label: t($messages, 'byTheNumbersMarketCap'), value: compactUsd(marketCap) }
        : null,
      volume != null
        ? { label: t($messages, 'byTheNumbersVolume'), value: compactUsd(volume) }
        : null,
    ].filter((x): x is { label: string; value: string } => x != null),
  )

  type Pt = { x: number; y: number; price: number; epoch: number }

  /** Smooth cubic path through points (Catmull-Rom → Bezier). */
  function smoothPath(pts: Pt[]): string {
    if (pts.length < 2) return ''
    if (pts.length === 2) {
      return `M ${pts[0].x} ${pts[0].y} L ${pts[1].x} ${pts[1].y}`
    }
    let d = `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i === 0 ? 0 : i - 1]
      const p1 = pts[i]
      const p2 = pts[i + 1]
      const p3 = pts[i + 2] ?? p2
      const cp1x = p1.x + (p2.x - p0.x) / 6
      const cp1y = p1.y + (p2.y - p0.y) / 6
      const cp2x = p2.x - (p3.x - p1.x) / 6
      const cp2y = p2.y - (p3.y - p1.y) / 6
      d += ` C ${cp1x.toFixed(2)} ${cp1y.toFixed(2)}, ${cp2x.toFixed(2)} ${cp2y.toFixed(2)}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`
    }
    return d
  }

  const spark = $derived.by(() => {
    if (history.length < 2) return null
    const minEpoch = history[0].epoch
    const maxEpoch = history[history.length - 1].epoch
    let minPrice = history[0].price
    let maxPrice = history[0].price
    for (const p of history) {
      if (p.price < minPrice) minPrice = p.price
      if (p.price > maxPrice) maxPrice = p.price
    }
    const epochSpan = maxEpoch - minEpoch > 0 ? maxEpoch - minEpoch : 1
    const priceSpan = Math.abs(maxPrice - minPrice) < 1e-12 ? 1 : maxPrice - minPrice
    const w = 320
    const h = 72
    const padY = 6
    const pts: Pt[] = history.map((p) => {
      const x = ((p.epoch - minEpoch) / epochSpan) * w
      const y = h - padY - ((p.price - minPrice) / priceSpan) * (h - padY * 2)
      return { x, y, price: p.price, epoch: p.epoch }
    })
    const line = smoothPath(pts)
    const last = pts[pts.length - 1]
    const first = pts[0]
    const area = `${line} L ${last.x.toFixed(2)} ${h} L ${first.x.toFixed(2)} ${h} Z`
    return {
      line,
      area,
      w,
      h,
      pts,
      last,
      minPrice,
      maxPrice,
    }
  })

  let tip = $state<{ x: number; y: number; price: number; label: string } | null>(null)
  let chartEl = $state<SVGSVGElement | null>(null)

  function onMove(e: PointerEvent) {
    if (!spark || !chartEl) return
    const rect = chartEl.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * spark.w
    let best = spark.pts[0]
    let bestDist = Math.abs(best.x - x)
    for (const p of spark.pts) {
      const d = Math.abs(p.x - x)
      if (d < bestDist) {
        best = p
        bestDist = d
      }
    }
    tip = {
      x: best.x,
      y: best.y,
      price: best.price,
      label: new Date(best.epoch * 1000).toLocaleDateString(localeTag($activeLocale), {
        month: 'short',
        day: 'numeric',
      }),
    }
  }

  function onLeave() {
    tip = null
  }
</script>

{#if priceUsd > 0}
  <section class="numbers" aria-label="ALGO" class:up class:down={!up}>
    <div class="head">
      <p class="slug">ALGO</p>
      {#if change != null}
        <span class="pill" class:up class:down={!up}>
          {up ? '▲' : '▼'}
          {Math.abs(change).toFixed(2)}%
          <span class="pill-sub">24h</span>
        </span>
      {/if}
    </div>
    <div class="row">
      <div class="spot">
        <span class="price">${priceUsd.toFixed(4)}</span>
      </div>
      {#if figures.length}
        <div class="side wide-only">
          {#each figures as fig}
            <div class="fig">
              <p class="fig-label">{fig.label}</p>
              <p class="fig-value">{fig.value}</p>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    {#if spark}
      <div class="chart-wrap">
        <svg
          bind:this={chartEl}
          class="spark"
          viewBox="0 0 {spark.w} {spark.h}"
          preserveAspectRatio="none"
          role="img"
          aria-label={t($messages, 'byTheNumbersRange')}
          onpointermove={onMove}
          onpointerleave={onLeave}
        >
          <defs>
            <linearGradient id="algo-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" class="g-top" />
              <stop offset="100%" class="g-bot" />
            </linearGradient>
          </defs>
          <line class="baseline" x1="0" y1={spark.h - 0.5} x2={spark.w} y2={spark.h - 0.5} />
          <path d={spark.area} class="fill" />
          <path d={spark.line} class="line" fill="none" />
          {#if tip}
            <line class="cross" x1={tip.x} y1="0" x2={tip.x} y2={spark.h} />
          {/if}
        </svg>
        <span
          class="mark end"
          style="left: {(spark.last.x / spark.w) * 100}%; top: {(spark.last.y / spark.h) * 100}%"
        ></span>
        {#if tip}
          <span
            class="mark tip-dot"
            style="left: {(tip.x / spark.w) * 100}%; top: {(tip.y / spark.h) * 100}%"
          ></span>
          <div class="tip" style="left: {(tip.x / spark.w) * 100}%">
            <strong>${tip.price.toFixed(4)}</strong>
            <span>{tip.label}</span>
          </div>
        {/if}
      </div>
      <div class="range-row">
        <span class="range">{t($messages, 'byTheNumbersRange')}</span>
        <span class="minmax muted">
          ${spark.minPrice.toFixed(4)} – ${spark.maxPrice.toFixed(4)}
        </span>
      </div>
    {/if}

    {#if figures.length}
      <div class="side narrow-only">
        {#each figures as fig}
          <div class="fig start">
            <p class="fig-label">{fig.label}</p>
            <p class="fig-value">{fig.value}</p>
          </div>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
  .numbers {
    --tone: var(--accent);
    padding: 26px 0 22px;
  }
  /* The band is unfilled, so the module needs almost nothing from it. The
     only real difference: markers sit on --surface here, not on a panel. */
  :global(.band) .numbers .mark.end {
    background: var(--surface);
  }
  :global(.band) .numbers .mark.tip-dot {
    border-color: var(--surface);
  }
  .head {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .slug {
    margin: 0;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .pill {
    display: inline-flex;
    align-items: baseline;
    gap: 5px;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent);
  }
  .pill.up {
    color: var(--gain);
    background: color-mix(in srgb, var(--gain) 12%, transparent);
  }
  .pill.down {
    color: var(--loss);
    background: color-mix(in srgb, var(--loss) 12%, transparent);
  }
  .pill-sub {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    opacity: 0.75;
  }
  .row {
    display: flex;
    align-items: flex-end;
    gap: 16px;
    margin-top: 8px;
  }
  .spot {
    display: flex;
    align-items: flex-end;
    gap: 12px;
    min-width: 0;
  }
  .price {
    font-family: var(--font-mono);
    font-size: clamp(32px, 4.6vw, 41px);
    font-weight: 600;
    line-height: 1;
    letter-spacing: -1.5px;
    font-variant-numeric: tabular-nums;
    animation: price-in 0.55s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  @keyframes price-in {
    from {
      opacity: 0;
      transform: translateY(6px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  .side {
    display: flex;
    gap: 28px;
  }
  .wide-only {
    display: none;
    margin-inline-start: auto;
  }
  .narrow-only {
    margin-top: 16px;
  }
  @media (min-width: 700px) {
    .wide-only {
      display: flex;
    }
    .narrow-only {
      display: none;
    }
  }
  .fig {
    text-align: end;
  }
  .fig.start {
    text-align: start;
  }
  .fig-label {
    margin: 0;
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .fig-value {
    margin: 2px 0 0;
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 600;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .narrow-only .fig-value {
    font-size: 20px;
  }
  .chart-wrap {
    position: relative;
    margin-top: 14px;
  }
  .spark {
    display: block;
    width: 100%;
    height: 72px;
    cursor: crosshair;
    touch-action: pan-y;
  }
  .baseline {
    stroke: var(--border);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .g-top {
    stop-color: var(--tone);
    stop-opacity: 0.28;
  }
  .g-bot {
    stop-color: var(--tone);
    stop-opacity: 0;
  }
  .fill {
    fill: url(#algo-fill);
    opacity: 0;
    animation: fill-in 0.55s ease 0.45s forwards;
  }
  .line {
    stroke: var(--tone);
    stroke-width: 2.25;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  /* No draw-on animation here. It normalised the dash with pathLength="1",
     but `vector-effect: non-scaling-stroke` makes Chrome measure dashes in
     screen space, and preserveAspectRatio="none" stretches this chart ~4x
     horizontally — so the dash ran out around 54% and the price line simply
     stopped mid-chart while the area fill carried on. Data should not arrive
     in pieces. */
  .cross {
    stroke: color-mix(in srgb, var(--tone) 45%, transparent);
    stroke-width: 1;
    stroke-dasharray: 3 3;
    vector-effect: non-scaling-stroke;
    pointer-events: none;
  }
  .mark {
    position: absolute;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    transform: translate(-50%, -50%);
    pointer-events: none;
    box-sizing: border-box;
  }
  .mark.end {
    background: var(--panel);
    border: 2px solid var(--tone);
    opacity: 0;
    animation: fill-in 0.35s ease 1s forwards;
  }
  .mark.tip-dot {
    width: 11px;
    height: 11px;
    background: var(--tone);
    border: 2px solid var(--panel);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--tone) 22%, transparent);
  }
  .tip {
    position: absolute;
    top: 0;
    transform: translate(-50%, -110%);
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 6px 9px;
    border-radius: 8px;
    background: var(--panel);
    border: 1px solid var(--border);
    box-shadow: 0 8px 20px var(--card-shadow);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    pointer-events: none;
    white-space: nowrap;
    z-index: 2;
    animation: tip-in 0.15s ease both;
  }
  .tip strong {
    font-size: 13px;
    color: var(--on-surface);
  }
  .tip span {
    color: var(--subtle);
  }
  @keyframes tip-in {
    from {
      opacity: 0;
      transform: translate(-50%, -95%);
    }
    to {
      opacity: 1;
      transform: translate(-50%, -110%);
    }
  }
  @keyframes fill-in {
    to {
      opacity: 1;
    }
  }
  .range-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    margin-top: 8px;
  }
  .range {
    font-size: 10.5px;
    color: var(--subtle);
  }
  .minmax {
    font-size: 10.5px;
    font-variant-numeric: tabular-nums;
  }
  @media (prefers-reduced-motion: reduce) {
    .price,
    .fill,
    .mark.end,
    .tip {
      animation: none;
    }
    .fill,
    .mark.end {
      opacity: 1;
    }
  }
</style>
