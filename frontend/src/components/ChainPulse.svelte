<script lang="ts">
  import { tick } from 'svelte'
  import { newsApi } from '../lib/api/news'
  import { messages, t } from '../lib/i18n'

  type Kind = { id: string; count: number }
  type Bar = { round: number; txns: number; inners: number; kinds: Kind[] }

  const SEG_OP: Record<string, number> = {
    pay: 0.92,
    axfer: 0.68,
    appl: 0.48,
    keyreg: 0.32,
    stpf: 0.32,
    other: 0.22,
  }
  const LEGEND = ['pay', 'axfer', 'appl'] as const
  const WINDOW = 20
  const GAP = 3
  const SLIDE_MS = 900

  let total = $state(0)
  let bars = $state.raw<Bar[]>([])
  let inspected = $state(0)
  let clip = $state<HTMLDivElement | null>(null)
  let strip = $state<HTMLDivElement | null>(null)
  let barPx = $state(0)
  let offsetPx = $state(0)
  let moving = $state(false)
  let slidePeak = 0
  let primed = false
  let sliding = false
  let pending: Bar[] | null = null

  function allowMotion(): boolean {
    return !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  }

  function measure(): number {
    if (!clip) return barPx
    const next = (clip.clientWidth - (WINDOW - 1) * GAP) / WINDOW
    if (next > 0) barPx = next
    return barPx
  }

  function slideDistance(count: number): number {
    if (!strip || count < 1) return count * (Math.max(barPx, 1) + GAP)
    const kids = strip.children
    if (kids.length <= count) {
      const first = kids[0] as HTMLElement | undefined
      const last = kids[kids.length - 1] as HTMLElement | undefined
      if (first && last && kids.length > 1) {
        const step = (last.offsetLeft - first.offsetLeft) / (kids.length - 1)
        return step * count
      }
      return count * (Math.max(barPx, 1) + GAP)
    }
    const first = kids[0] as HTMLElement
    const pivot = kids[count] as HTMLElement
    return pivot.offsetLeft - first.offsetLeft
  }

  function easeOutCubic(t: number): number {
    return 1 - (1 - t) ** 3
  }

  function afterPaint(): Promise<void> {
    return new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
    })
  }

  function setStripOffset(px: number) {
    offsetPx = px
    if (strip) strip.style.transform = `translate3d(${px}px, 0, 0)`
  }

  function animateOffset(from: number, to: number, duration: number): Promise<void> {
    return new Promise((resolve) => {
      const start = performance.now()
      const frame = (now: number) => {
        const p = Math.min(1, (now - start) / duration)
        setStripOffset(from + (to - from) * easeOutCubic(p))
        if (p < 1) requestAnimationFrame(frame)
        else {
          setStripOffset(to)
          resolve()
        }
      }
      requestAnimationFrame(frame)
    })
  }

  async function show(next: Bar[]) {
    const target = next.slice(-WINDOW)
    if (!primed || !allowMotion()) {
      bars = target
      primed = true
      offsetPx = 0
      moving = false
      slidePeak = 0
      await tick()
      measure()
      return
    }
    if (sliding) {
      pending = next
      return
    }
    const have = new Set(bars.map((b) => b.round))
    const extra = next.filter((b) => !have.has(b.round))
    if (!extra.length) {
      bars = target
      return
    }
    if (extra.length >= WINDOW) {
      bars = target
      offsetPx = 0
      moving = false
      return
    }

    sliding = true
    moving = true
    slidePeak = Math.max(1, ...bars.map((b) => b.txns), ...extra.map((b) => b.txns))
    bars = [...bars, ...extra]
    setStripOffset(0)
    await tick()
    await afterPaint()

    const dx = slideDistance(extra.length)
    if (dx < 1) {
      moving = false
      slidePeak = 0
      bars = target
      setStripOffset(0)
      sliding = false
      return
    }

    await animateOffset(0, -dx, SLIDE_MS)

    /* Trim the off-screen column; reset transform in the same frame. */
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => {
        moving = false
        slidePeak = 0
        bars = target
        setStripOffset(0)
        resolve()
      })
    })
    sliding = false

    if (pending) {
      const queued = pending
      pending = null
      await show(queued)
    }
  }

  const peak = $derived(Math.max(1, ...bars.map((b) => b.txns)))
  const mid = $derived(Math.max(1, Math.round(peak / 2)))
  const active = $derived(bars.find((b) => b.round === inspected) ?? null)
  const totalLabel = $derived(
    t($messages, 'chainPulseTxns', { count: total.toLocaleString() }),
  )
  const legendLine = $derived(LEGEND.map((id) => kindLabel(id)).join(' · '))
  const stampLine = $derived.by(() => {
    if (!active) return totalLabel
    const round = t($messages, 'chainPulseRound', {
      round: active.round.toLocaleString(),
    })
    const txns = t($messages, 'chainPulseBlockTxns', {
      count: active.txns.toLocaleString(),
    })
    return `${round} · ${txns}`
  })
  const mixLine = $derived.by(() => {
    if (!active || !active.kinds.length) return legendLine
    const parts = active.kinds.map((k) => `${k.count} ${kindLabel(k.id)}`)
    if (active.inners > 0) {
      parts.push(t($messages, 'chainPulseInners', { count: active.inners.toLocaleString() }))
    }
    return parts.join(' · ')
  })

  function kindLabel(id: string): string {
    const key = `chainPulseKind${id.charAt(0).toUpperCase()}${id.slice(1)}`
    return t($messages, key)
  }

  function barHeight(txns: number): number {
    const scale = moving ? slidePeak : peak
    return Math.max(8, Math.round((txns / scale) * 100))
  }

  $effect(() => {
    if (clip) measure()
  })

  $effect(() => {
    const ac = new AbortController()
    const load = async () => {
      if (document.visibilityState === 'hidden') return
      try {
        const pulse = await newsApi.fetchChainPulse(ac.signal)
        if (ac.signal.aborted) return
        const next = pulse.blocks.filter((b) => b.round > 0)
        total = pulse.txns_last_minute
        await show(next)
      } catch {
        /* keep the last good pulse */
      }
    }
    void load()
    const tick = () => {
      if (document.visibilityState !== 'hidden') void load()
    }
    const timer = setInterval(tick, 2000)
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
</script>

<aside class="pulse" aria-label={t($messages, 'chainPulse')}>
  <p class="slug">
    <span class="live-pixel" aria-hidden="true"></span>
    {t($messages, 'chainPulse')}
  </p>
  <p class="deck muted">{t($messages, 'chainPulseLead')}</p>
  {#if bars.length}
    <div class="chart" aria-hidden="true">
      <div class="scale" aria-hidden="true">
        <span>{peak.toLocaleString()}</span>
        <span>{mid.toLocaleString()}</span>
        <span>0</span>
      </div>
      <div
        class="bars"
        role="presentation"
        bind:this={clip}
        style:--bar="{barPx}px"
        onpointerleave={() => (inspected = 0)}
      >
        <div
          class={['strip', moving && 'moving']}
          style:transform="translate3d({offsetPx}px, 0, 0)"
          bind:this={strip}
          role="presentation"
        >
          {#each bars as bar (bar.round)}
            <div
              class={['bar', bar.round === inspected && 'on']}
              style="height: {barHeight(bar.txns)}%"
              role="presentation"
              onpointerenter={() => (inspected = bar.round)}
            >
              {#each bar.kinds as kind (kind.id)}
                <span
                  class="seg"
                  style="flex-grow: {kind.count}; opacity: {SEG_OP[kind.id] ?? 0.4}"
                ></span>
              {/each}
            </div>
          {/each}
        </div>
      </div>
    </div>
    <div class="foot">
      <p class="stamp">{stampLine}</p>
      <p class="mix">
        {#if !active}
          {#each LEGEND as id (id)}
            <span class="key">
              <span class="swatch" style="opacity: {SEG_OP[id]}"></span>{kindLabel(id)}
            </span>
          {/each}
        {:else}
          {mixLine}
        {/if}
      </p>
    </div>
  {:else}
    <p class="stamp muted">{t($messages, 'loading')}</p>
  {/if}
</aside>

<style>
  .pulse {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
    padding: 4px 0 2px;
    height: 100%;
  }
  .slug {
    margin: 0;
    display: flex;
    align-items: center;
    gap: 8px;
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
    max-width: 32ch;
  }
  .chart {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 8px;
    align-items: stretch;
    flex: 1 1 128px;
    min-height: 128px;
    margin-top: 6px;
  }
  .scale {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    align-items: flex-end;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.2px;
    color: var(--subtle);
    font-variant-numeric: tabular-nums;
    padding: 2px 0;
  }
  .bars {
    --gap: 3px;
    min-width: 0;
    min-height: 112px;
    height: 100%;
    overflow: hidden;
    direction: ltr;
    border-bottom: 1px solid var(--border);
    background-image: linear-gradient(
      to top,
      transparent 0,
      transparent calc(50% - 0.5px),
      var(--border) calc(50% - 0.5px),
      var(--border) calc(50% + 0.5px),
      transparent calc(50% + 0.5px)
    );
  }
  .strip {
    display: flex;
    align-items: flex-end;
    gap: var(--gap);
    width: max-content;
    height: 100%;
    min-height: 112px;
    will-change: transform;
  }
  .strip.moving .bar {
    transition: none !important;
  }
  .bar {
    flex: 0 0 var(--bar);
    width: var(--bar);
    min-width: 0;
    background: color-mix(in srgb, var(--accent) 22%, transparent);
    display: flex;
    flex-direction: column-reverse;
  }
  @media (prefers-reduced-motion: no-preference) {
    .bar:not(.on) {
      transition: height 0.45s cubic-bezier(0.22, 1, 0.36, 1);
    }
  }
  .bar.on {
    outline: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
    outline-offset: -1px;
  }
  .seg {
    display: block;
    width: 100%;
    min-height: 2px;
    background: var(--accent);
    flex-shrink: 1;
  }
  .foot {
    flex: 0 0 auto;
    min-height: 2.8em;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .stamp,
  .mix {
    margin: 0;
    min-height: 1.35em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.3px;
    text-transform: uppercase;
    font-variant-numeric: tabular-nums;
  }
  .stamp {
    color: var(--muted);
  }
  .mix {
    color: var(--body);
  }
  .key {
    margin-inline-end: 12px;
  }
  .key:last-child {
    margin-inline-end: 0;
  }
  .swatch {
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 6px;
    background: var(--accent);
    vertical-align: 6%;
  }
</style>
