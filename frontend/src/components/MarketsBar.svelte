<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { newsApi } from '../lib/api/news'
  import { pathOnly } from '../lib/router'
  import Icon from './Icon.svelte'

  type Tile = {
    id: string
    label: string
    value: string
    hint?: string | null
    available: boolean
  }

  type MetricIcon =
    | 'payments'
    | 'bar_chart'
    | 'pie_chart'
    | 'layers'
    | 'speed'
    | 'article'
    | 'swap'
    | 'insights'
    | 'hub'

  const iconFor: Record<string, MetricIcon> = {
    algo_price: 'payments',
    volume_24h: 'bar_chart',
    market_cap: 'pie_chart',
    last_round: 'layers',
    round_latency: 'speed',
    validators: 'hub',
    articles: 'article',
    dex_volume: 'swap',
  }

  const showOn = $derived(
    ['/', '/news', '/hot', '/top', '/topics'].includes($pathOnly) ||
      $pathOnly.startsWith('/topic/'),
  )

  let tiles: Tile[] = $state([])
  let timer: ReturnType<typeof setInterval> | undefined

  const visible = $derived(tiles.filter((t) => t.available))

  async function load() {
    try {
      const res = await newsApi.fetchMetricsDashboard()
      tiles = res.tiles
    } catch {
      /* keep previous tiles */
    }
  }

  onMount(() => {
    void load()
    timer = setInterval(() => void load(), 60_000)
  })

  onDestroy(() => {
    if (timer) clearInterval(timer)
  })

  function hintTone(hint: string | null | undefined): 'up' | 'down' | '' {
    const h = (hint ?? '').trim()
    if (h.startsWith('+')) return 'up'
    if (h.startsWith('-')) return 'down'
    return ''
  }
</script>

{#if showOn}
  <div class="markets" aria-label="Markets" class:empty={!visible.length}>
    {#if visible.length}
      <div class="scroller">
        <div class="inner">
          {#each visible as tile, i (tile.id)}
            {#if i > 0}<span class="divider"></span>{/if}
            <span class="chip">
              <span class="ico" aria-hidden="true">
                <Icon name={iconFor[tile.id] ?? 'insights'} size={13} />
              </span>
              <span class="label">{tile.label}</span>
              {#key tile.value}
                <span class="value">{tile.value}</span>
              {/key}
              {#if tile.hint}
                {@const tone = hintTone(tile.hint)}
                <span class="hint" class:up={tone === 'up'} class:down={tone === 'down'}>{tile.hint}</span>
              {/if}
            </span>
          {/each}
        </div>
      </div>
    {/if}
  </div>
{/if}

<style>
  /* Fixed height (incl. .empty) so async tiles don't shove the page (CLS). */
  .markets {
    height: 34px;
    background: color-mix(in srgb, var(--app-bar) 92%, var(--accent-soft));
    border-bottom: 1px solid var(--border);
    position: relative;
  }
  .markets::before,
  .markets::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    width: 28px;
    z-index: 1;
    pointer-events: none;
  }
  .markets::before {
    left: 0;
    background: linear-gradient(
      90deg,
      color-mix(in srgb, var(--app-bar) 92%, var(--accent-soft)),
      transparent
    );
  }
  .markets::after {
    right: 0;
    background: linear-gradient(
      270deg,
      color-mix(in srgb, var(--app-bar) 92%, var(--accent-soft)),
      transparent
    );
  }
  .scroller {
    height: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
  }
  .scroller::-webkit-scrollbar {
    display: none;
  }
  .inner {
    min-width: 100%;
    height: 100%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 20px;
    box-sizing: border-box;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .ico {
    display: grid;
    place-items: center;
    color: var(--subtle);
  }
  .label {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .value {
    font-size: 12.5px;
    font-weight: 700;
    color: var(--on-surface);
    margin-inline-start: 2px;
    font-variant-numeric: tabular-nums;
    animation: value-tick 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  @keyframes value-tick {
    from {
      color: var(--accent);
      opacity: 0.4;
      transform: translateY(3px) scale(0.96);
      filter: blur(0.4px);
    }
    55% {
      color: var(--primary);
      opacity: 1;
      transform: translateY(-1px) scale(1.02);
      filter: none;
    }
    to {
      color: var(--on-surface);
      opacity: 1;
      transform: none;
      filter: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .value {
      animation: none;
    }
  }
  .hint {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .hint.up {
    color: var(--gain);
  }
  .hint.down {
    color: var(--loss);
  }
  .divider {
    width: 1px;
    height: 14px;
    background: var(--border);
    margin: 0 14px;
    flex-shrink: 0;
  }
</style>
