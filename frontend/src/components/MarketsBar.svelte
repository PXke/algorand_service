<script lang="ts">
  import { innerWidth } from 'svelte/reactivity/window'
  import { newsApi } from '../lib/api/news'
  import { pathOnly } from '../lib/router'

  type Tile = {
    id: string
    label: string
    value: string
    hint?: string | null
    available: boolean
  }

  /* Phone keeps the chain pulse — price, round, nodes — not the full desk. */
  const WIRE_CORE = new Set(['algo_price', 'last_round', 'nodes'])

  const showOn = $derived(
    ['/', '/news', '/hot', '/top', '/topics'].includes($pathOnly) ||
      $pathOnly.startsWith('/topic/'),
  )

  let tiles: Tile[] = $state([])

  const visible = $derived(tiles.filter((t) => t.available))
  const shown = $derived.by(() => {
    const w = innerWidth.current
    if (w !== undefined && w < 640) {
      const core = visible.filter((t) => WIRE_CORE.has(t.id))
      return core.length ? core : visible.slice(0, 3)
    }
    return visible
  })

  // Fetch + poll only while the bar is on screen — article/about/etc. shouldn't
  // keep hitting /metrics/dashboard every minute in the background.
  $effect(() => {
    if (!showOn) return
    const ac = new AbortController()
    const load = async () => {
      try {
        const res = await newsApi.fetchMetricsDashboard(ac.signal)
        if (!ac.signal.aborted) tiles = res.tiles
      } catch {
        /* keep previous tiles / ignore abort */
      }
    }
    void load()
    const tick = () => {
      if (document.visibilityState !== 'hidden') void load()
    }
    const timer = setInterval(tick, 60_000)
    const onVis = () => {
      if (document.visibilityState === 'visible') void load()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      ac.abort()
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVis)
    }
  })

  function hintTone(hint: string | null | undefined): 'up' | 'down' | '' {
    const h = (hint ?? '').trim()
    if (h.startsWith('+')) return 'up'
    if (h.startsWith('-')) return 'down'
    return ''
  }
</script>

{#if showOn}
  <div class="markets" aria-label="Markets" class:empty={!shown.length}>
    {#if shown.length}
      <span class="live-pixel" aria-hidden="true"></span>
      <div class="scroller">
        <div class="inner">
          {#each shown as tile, i (tile.id)}
            {#if i > 0}<span class="sep" aria-hidden="true">·</span>{/if}
            <span class="chip">
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
    height: 36px;
    background: var(--surface);
    color: var(--on-surface);
    border-bottom: 1px solid var(--border);
    position: relative;
  }
  .markets::before,
  .markets::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    width: 44px;
    z-index: 1;
    pointer-events: none;
  }
  .markets::before {
    left: 0;
    background: linear-gradient(
      90deg,
      color-mix(in srgb, var(--surface) 100%, transparent) 35%,
      transparent
    );
  }
  .markets::after {
    right: 0;
    background: linear-gradient(
      270deg,
      color-mix(in srgb, var(--surface) 100%, transparent) 35%,
      transparent
    );
  }
  .scroller {
    height: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-x: contain;
  }
  .scroller::-webkit-scrollbar {
    display: none;
  }
  .inner {
    min-width: max-content;
    width: 100%;
    height: 100%;
    display: inline-flex;
    align-items: center;
    justify-content: safe center;
    padding-inline: var(--shell-gutter);
    padding-inline-start: max(var(--shell-gutter), 28px);
    box-sizing: border-box;
  }
  .markets :global(.live-pixel) {
    position: absolute;
    inset-inline-start: 12px;
    top: 50%;
    z-index: 2;
    transform: translateY(-50%);
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .sep {
    margin: 0 12px;
    color: var(--subtle);
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1;
  }
  .label {
    font-family: var(--font-mono);
    font-size: 9.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .value {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    color: var(--on-surface);
    margin-inline-start: 2px;
    font-variant-numeric: tabular-nums;
    animation: value-tick 0.35s ease both;
  }
  @keyframes value-tick {
    from {
      opacity: 0.35;
    }
    to {
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .value {
      animation: none;
    }
  }
  .hint {
    font-family: var(--font-mono);
    font-size: 10.5px;
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
  @media (max-width: 639px) {
    .markets::before,
    .markets::after {
      width: 20px;
    }
    .sep {
      margin: 0 8px;
    }
  }
</style>
