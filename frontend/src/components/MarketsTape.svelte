<script lang="ts">
  import { messages, t } from '../lib/i18n'
  import ByTheNumbers from './ByTheNumbers.svelte'

  let {
    price,
    history = [],
  }: {
    price: Record<string, unknown> | null
    history?: Array<{ epoch: number; price: number }>
  } = $props()

  function compactUsd(value: number): string {
    if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
    if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
    if (value >= 1e3) return `$${(value / 1e3).toFixed(1)}K`
    return `$${value.toFixed(0)}`
  }

  const cap = $derived(Number(price?.market_cap_usd ?? 0))
  const vol = $derived(Number(price?.volume_24h_usd ?? 0))
  const figures = $derived(
    [
      cap > 0
        ? { label: t($messages, 'byTheNumbersMarketCap'), value: compactUsd(cap) }
        : null,
      vol > 0
        ? { label: t($messages, 'byTheNumbersVolume'), value: compactUsd(vol) }
        : null,
    ].filter((x): x is { label: string; value: string } => x != null),
  )
  const share = $derived.by(() => {
    if (!(cap > 0) || !(vol > 0)) return null
    const pct = Math.min(100, (vol / cap) * 100)
    return { pct, label: `${pct.toFixed(1)}%` }
  })
  const showMarket = $derived(figures.length > 0)
</script>

<div class="tape">
  {#if price}
    <div class="price-pane">
      <ByTheNumbers {price} {history} />
    </div>
  {/if}
  {#if showMarket}
    <aside class="market-pane">
      <p class="slug">{t($messages, 'tapeMarket')}</p>
      <p class="deck muted">{t($messages, 'tapeMarketLead')}</p>
      <div class="figs">
        {#each figures as fig (fig.label)}
          <div class="fig">
            {#key fig.value}
              <p class="fig-value motion-value-tick">{fig.value}</p>
            {/key}
            <p class="fig-label">{fig.label}</p>
          </div>
        {/each}
      </div>
      {#if share}
        <div
          class="share"
          role="img"
          aria-label={t($messages, 'tapeShareTraded', { pct: share.label })}
        >
          <div class="bar" aria-hidden="true">
            <span class="fill motion-fill" style="width: {share.pct}%"></span>
          </div>
          <p class="share-caption">{t($messages, 'tapeShareTraded', { pct: share.label })}</p>
        </div>
      {/if}
    </aside>
  {/if}
</div>

<style>
  .tape {
    display: grid;
    gap: 28px;
    padding: 8px 0 4px;
    border-top: 1px solid var(--on-surface);
    border-bottom: 1px solid var(--border);
  }
  @media (min-width: 860px) {
    .tape {
      grid-template-columns: minmax(0, 1.45fr) minmax(240px, 0.7fr);
      align-items: stretch;
      column-gap: 36px;
    }
  }
  .price-pane :global(.narrow-only),
  .price-pane :global(.wide-only) {
    display: none;
  }
  .price-pane :global(.numbers) {
    padding: 16px 0 10px;
  }
  .price-pane :global(.spark) {
    height: 96px;
  }
  .market-pane {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 16px 0 10px;
  }
  @media (max-width: 859px) {
    .tape {
      gap: 12px;
      padding: 4px 0 8px;
    }
    .price-pane :global(.numbers) {
      padding: 10px 0 4px;
    }
    .price-pane :global(.spark) {
      height: 72px;
    }
    .market-pane {
      display: none;
    }
  }
  @media (min-width: 860px) {
    .market-pane {
      border-inline-start: 1px solid var(--border);
      padding-inline-start: 28px;
    }
  }
  .slug {
    margin: 0;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .deck {
    margin: 0;
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.45;
    max-width: 28ch;
  }
  .figs {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px 20px;
    margin-top: 6px;
  }
  .fig-value {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 22px;
    font-weight: 600;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.6px;
  }
  .fig-label {
    margin: 5px 0 0;
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .share {
    display: flex;
    flex-direction: column;
    gap: 7px;
    margin-top: 4px;
  }
  .bar {
    height: 8px;
    background: color-mix(in srgb, var(--accent) 14%, var(--border));
  }
  .fill {
    display: block;
    height: 100%;
    background: var(--accent);
  }
  :global(.motion-fill) {
    transition: width 0.55s cubic-bezier(0.22, 1, 0.36, 1);
  }
  :global(.motion-value-tick) {
    animation: value-tick 0.35s ease both;
  }
  .share-caption {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.3px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
</style>
