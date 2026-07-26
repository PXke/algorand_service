<script lang="ts">
  import { messages, t } from '../lib/i18n'

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
    const w = 100
    const h = 44
    const pts = history.map((p) => {
      const x = ((p.epoch - minEpoch) / epochSpan) * w
      const y = h * 0.92 - ((p.price - minPrice) / priceSpan) * h * 0.84
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    const line = pts.join(' ')
    const first = pts[0]
    const last = pts[pts.length - 1]
    const fill = `${first} ${line} ${last.split(',')[0]},${h} 0,${h}`
    return { line, fill, w, h }
  })
</script>

{#if priceUsd > 0}
  <section class="numbers" aria-label="ALGO">
    <p class="slug">ALGO</p>
    <div class="row">
      <div class="spot">
        <span class="price">${priceUsd.toFixed(4)}</span>
        {#if change != null}
          <span class="chg" class:up class:down={!up}>
            {up ? '▲' : '▼'}
            {Math.abs(change).toFixed(2)}%
          </span>
        {/if}
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
      <svg
        class="spark"
        viewBox="0 0 {spark.w} {spark.h}"
        preserveAspectRatio="none"
        role="img"
        aria-label={t($messages, 'byTheNumbersRange')}
      >
        <polygon points={spark.fill} class="fill" />
        <polyline points={spark.line} class="line" fill="none" />
      </svg>
      <p class="range">{t($messages, 'byTheNumbersRange')}</p>
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
    padding: 20px 0;
    border-top: 3px solid var(--accent);
    border-bottom: 1px solid var(--border);
  }
  .slug {
    margin: 0;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .row {
    display: flex;
    align-items: flex-end;
    gap: 16px;
    margin-top: 6px;
  }
  .spot {
    display: flex;
    align-items: flex-end;
    gap: 12px;
    min-width: 0;
  }
  .price {
    font-family: var(--font-display);
    font-size: clamp(34px, 5vw, 44px);
    font-weight: 700;
    line-height: 1;
    letter-spacing: -1px;
  }
  .chg {
    padding-bottom: 4px;
    font-size: 0.95rem;
    font-weight: 700;
  }
  .chg.up {
    color: var(--gain);
  }
  .chg.down {
    color: var(--loss);
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
    font-family: var(--font-display);
    font-size: 22px;
    font-weight: 700;
    line-height: 1;
  }
  .narrow-only .fig-value {
    font-size: 20px;
  }
  .spark {
    display: block;
    width: 100%;
    height: 44px;
    margin-top: 16px;
  }
  .spark .fill {
    fill: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  .spark .line {
    stroke: var(--accent);
    stroke-width: 1.6;
    stroke-linejoin: round;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  .range {
    margin: 6px 0 0;
    font-size: 10.5px;
    color: var(--subtle);
  }
</style>
