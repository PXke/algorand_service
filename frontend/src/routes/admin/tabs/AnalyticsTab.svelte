<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'
  import {
    asMap,
    asRows,
    deltaPct,
    donutArc,
    donutSlices,
    flagEmoji,
    layoutSimpleBars,
    layoutStackedBars,
    num,
    shortDay,
    sparklinePath,
    str,
    CHART_PALETTE,
    type StackSeries,
  } from '../charts'

  let { admin }: { admin: AdminApi } = $props()

  type Group = 'Overview' | 'Acquisition' | 'Content' | 'Audience'
  const groups: Group[] = ['Overview', 'Acquisition', 'Content', 'Audience']

  let days = $state(14)
  let group = $state<Group>('Overview')
  let data = $state<Record<string, unknown> | null>(null)
  let loading = $state(true)
  let error = $state<string | null>(null)

  async function load() {
    loading = true
    error = null
    try {
      data = await admin.fetchAnalytics(Number(days))
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  $effect(() => {
    days
    void load()
  })

  const totals = $derived(asMap(data?.totals))
  const prev = $derived(asMap(data?.prev_totals))
  const sessions = $derived(asMap(data?.sessions))
  const alerts = $derived(asRows(data?.alerts))

  const human = $derived(num(totals.human))
  const humanUnique = $derived(num(totals.human_unique))
  const sessTotal = $derived(num(sessions.total))
  const returningRate = $derived(num(sessions.returning_rate))
  const pagesPerVisit = $derived(num(sessions.pages_per_visit))
  const bounceRate = $derived(num(sessions.bounce_rate))

  const dHuman = $derived(deltaPct(human, num(prev.human)))
  const dUnique = $derived(deltaPct(humanUnique, num(prev.human_unique)))
  const dSess = $derived(deltaPct(sessTotal, num(prev.sessions)))

  const dailyBars = $derived(
    layoutSimpleBars(
      asRows(data?.daily).map((r) => ({
        label: shortDay(str(r.day)),
        value: num(r.human),
      })),
    ),
  )

  const hourBars = $derived(
    layoutSimpleBars(
      asRows(data?.hours).map((r) => ({
        label: `${num(r.hour)}h`,
        value: num(r.views),
      })),
      { h: 180 },
    ),
  )

  const sessionsStack = $derived.by(() => {
    const series: StackSeries[] = [
      { key: 'new', color: CHART_PALETTE[0]! },
      { key: 'returning', color: CHART_PALETTE[1]! },
    ]
    return layoutStackedBars(
      asRows(data?.sessions_daily).map((r) => ({
        label: shortDay(str(r.day)),
        values: { new: num(r.new), returning: num(r.returning) },
      })),
      series,
      { h: 180 },
    )
  })

  const referrersOverTime = $derived.by(() => {
    const m = asMap(data?.referrers_daily)
    const names = Array.isArray(m.referrers) ? (m.referrers as string[]) : []
    const series: StackSeries[] = names.map((key, i) => ({
      key,
      color: CHART_PALETTE[i % CHART_PALETTE.length]!,
    }))
    const daily = asRows(m.daily).map((row) => {
      const values: Record<string, number> = {}
      for (const n of names) values[n] = num(row[n])
      return { label: shortDay(str(row.day)), values }
    })
    return layoutStackedBars(daily, series)
  })

  const refCats = $derived(donutSlices(asRows(data?.referrer_categories), 'category'))
  const devices = $derived(donutSlices(asRows(data?.device), 'device'))
  const browsers = $derived(donutSlices(asRows(data?.browser), 'browser'))
  const languages = $derived(donutSlices(asRows(data?.languages), 'lang'))

  function fmtDelta(d: number | null): string {
    if (d == null) return ''
    return `${d >= 0 ? '▲' : '▼'}${Math.abs(d).toFixed(0)}%`
  }

  function openPath(path: string | undefined) {
    if (!path) return
    window.open(path.startsWith('http') ? path : path, '_blank', 'noopener,noreferrer')
  }

  function openExternal(url: string | undefined) {
    if (!url) return
    window.open(url.startsWith('http') ? url : `https://${url}`, '_blank', 'noopener,noreferrer')
  }

  function donutPaths(slices: ReturnType<typeof donutSlices>['slices'], total: number) {
    if (total <= 0) return []
    let angle = 0
    return slices.map((s) => {
      const sweep = (s.value / total) * 360
      const start = angle
      const end = angle + Math.max(sweep, s.value > 0 ? 0.5 : 0)
      angle = end
      return { ...s, d: donutArc(55, 55, 52, 30, start, end) }
    })
  }
</script>

<div class="admin-stack">
  <div class="admin-toolbar">
    <h2>Traffic</h2>
    <div class="tools">
      <select class="admin-select" bind:value={days}>
        <option value={7}>Last 7 days</option>
        <option value={14}>Last 14 days</option>
        <option value={30}>Last 30 days</option>
      </select>
      <button class="btn" type="button" disabled={loading} onclick={() => load()}>Refresh</button>
    </div>
  </div>

  {#if loading && !data}
    <p class="admin-muted">Loading analytics…</p>
  {:else if error}
    <p class="admin-err">{error}</p>
  {:else if data?.error}
    <p class="admin-err">Analytics unavailable (no data yet).</p>
  {:else if data}
    {#each alerts as a}
      <p class="admin-alert" class:warn={str(a.level) === 'warn'}>{str(a.text)}</p>
    {/each}

    <div class="admin-stats">
      <div class="admin-stat">
        <span class="lbl">Human views</span>
        <div class="val-row">
          <span class="val">{human}</span>
          {#if dHuman != null}
            <span class="admin-delta" class:up={dHuman >= 0} class:down={dHuman < 0}
              >{fmtDelta(dHuman)}</span
            >
          {/if}
        </div>
      </div>
      <div class="admin-stat">
        <span class="lbl">Unique visitors</span>
        <div class="val-row">
          <span class="val">{humanUnique}</span>
          {#if dUnique != null}
            <span class="admin-delta" class:up={dUnique >= 0} class:down={dUnique < 0}
              >{fmtDelta(dUnique)}</span
            >
          {/if}
        </div>
      </div>
      <div class="admin-stat">
        <span class="lbl">Visits</span>
        <div class="val-row">
          <span class="val">{sessTotal}</span>
          {#if dSess != null}
            <span class="admin-delta" class:up={dSess >= 0} class:down={dSess < 0}
              >{fmtDelta(dSess)}</span
            >
          {/if}
        </div>
      </div>
      <div class="admin-stat">
        <span class="lbl">Returning</span>
        <div class="val-row"><span class="val">{(returningRate * 100).toFixed(0)}%</span></div>
      </div>
      <div class="admin-stat">
        <span class="lbl">Pages / visit</span>
        <div class="val-row"><span class="val">{pagesPerVisit.toFixed(1)}</span></div>
      </div>
      <div class="admin-stat">
        <span class="lbl">Likely single-hit</span>
        <div class="val-row"><span class="val">{(bounceRate * 100).toFixed(0)}%</span></div>
      </div>
    </div>

    <div class="admin-segment" role="tablist">
      {#each groups as g}
        <button type="button" class:active={group === g} onclick={() => (group = g)}>{g}</button>
      {/each}
    </div>

    {#if group === 'Overview'}
      <section class="admin-panel">
        <h3>By day</h3>
        {#if dailyBars}
          <div class="admin-chart" role="img" aria-label="Human pageviews by day">
            <svg viewBox={`0 0 ${dailyBars.w} ${dailyBars.h}`}>
              {#each dailyBars.ticks as tick}
                <line class="grid" x1={dailyBars.padL} x2={dailyBars.w - 8} y1={tick.y} y2={tick.y} />
                <text class="axis" x={dailyBars.padL - 6} y={tick.y + 3} text-anchor="end">{tick.val}</text>
              {/each}
              {#each dailyBars.bars as bar, i}
                <rect fill="var(--primary)" x={bar.x} y={bar.y} width={bar.w} height={bar.h} rx="2">
                  <title>{bar.label}: {bar.v}</title>
                </rect>
                {#if i % dailyBars.showEvery === 0}
                  <text
                    class="axis bottom"
                    x={bar.x + bar.w / 2}
                    y={dailyBars.padT + dailyBars.plotH + 16}
                    text-anchor="middle">{bar.label}</text
                  >
                {/if}
              {/each}
            </svg>
          </div>
        {:else}
          <p class="admin-muted">No data yet</p>
        {/if}
      </section>

      <div class="admin-donut-row">
        {@render donutCard('Referrer channels', refCats)}
        {@render donutCard('Devices', devices)}
      </div>
    {:else if group === 'Acquisition'}
      <div class="admin-donut-row">
        {@render donutCard('Referrer channels', refCats)}
      </div>

      {@render rankPanel('Campaigns (utm / ref tags)', asRows(data?.campaigns), 'campaign')}

      <section class="admin-panel">
        <h3>Referrers over time</h3>
        {#if referrersOverTime}
          <div class="admin-legend">
            {#each referrersOverTime.series as s}
              <span class="admin-legend-item">
                <span class="admin-legend-dot" style="background:{s.color}"></span>
                {s.key}
              </span>
            {/each}
          </div>
          {@render stackedChart(referrersOverTime)}
        {:else}
          <p class="admin-muted">No data yet</p>
        {/if}
      </section>

      {@render rankPanel('Top referrers', asRows(data?.top_referrers), 'referrer')}
      {@render rankPanel('Top referrers (full URL)', asRows(data?.top_referrer_urls), 'referrer_url', {
        external: true,
      })}

      <section class="admin-panel">
        <h3>Top source → landing page</h3>
        {#if asRows(data?.referrer_paths).length === 0}
          <p class="admin-muted">No data yet</p>
        {:else}
          <div class="admin-rank">
            {#each asRows(data?.referrer_paths) as row}
              <button class="admin-rank-row link" type="button" onclick={() => openPath(str(row.path))}>
                <span class="admin-rank-label">
                  <span class="src">{str(row.referrer)}</span>
                  → {str(row.label ?? row.path)}
                </span>
                <span class="admin-rank-val">{num(row.views)}</span>
              </button>
            {/each}
          </div>
        {/if}
      </section>

      <section class="admin-panel">
        <h3>Article traffic sources</h3>
        {#if asRows(data?.article_referrers).length === 0}
          <p class="admin-muted">No data yet</p>
        {:else}
          {#each asRows(data?.article_referrers) as row}
            <button class="score-block" type="button" onclick={() => openPath(str(row.path))}>
              <div class="admin-score-copy">
                <p class="title">{str(row.label ?? row.path)}</p>
                <p class="meta">
                  {#each asRows(row.referrers) as ref}
                    <span>{str(ref.referrer)} · {num(ref.views)}</span>
                  {/each}
                </p>
              </div>
              <span class="admin-score-views">{num(row.views)}</span>
            </button>
          {/each}
        {/if}
      </section>

      <section class="admin-panel">
        <h3>Articles by referrer</h3>
        {#if asRows(data?.referrer_articles).length === 0}
          <p class="admin-muted">No data yet</p>
        {:else}
          {#each asRows(data?.referrer_articles) as row}
            <details class="ref-group">
              <summary>
                <span>{str(row.referrer)}</span>
                <strong>{num(row.views)}</strong>
              </summary>
              {#each asRows(row.articles) as art}
                <button class="admin-rank-row link" type="button" onclick={() => openPath(str(art.path))}>
                  <span class="admin-rank-label">{str(art.label)}</span>
                  <span class="admin-rank-val">{num(art.views)}</span>
                </button>
              {/each}
            </details>
          {/each}
        {/if}
      </section>

      {@render rankPanel('Direct breakdown (UA class)', asRows(data?.direct_uaclass), 'ua_class')}

      <section class="admin-panel">
        <h3>Recent direct requests (7-day sample)</h3>
        {#if asRows(data?.direct_samples).length === 0}
          <p class="admin-muted">No data yet</p>
        {:else}
          {#each asRows(data?.direct_samples) as row}
            {@const uaClass = str(row.ua_class)}
            {@const suspect =
              uaClass === 'non-browser' || uaClass === 'headless' || uaClass === 'no-ua'}
            <div class="sample">
              <div class="sample-top">
                <strong>{str(row.path)}</strong>
                <span class="admin-chip" class:suspect>{uaClass || '—'}</span>
              </div>
              <p class="admin-muted">{str(row.user_agent) || '(no user-agent)'}</p>
              {#if str(row.referer)}
                <p class="admin-muted">ref: {str(row.referer)}</p>
              {/if}
            </div>
          {/each}
        {/if}
      </section>
    {:else if group === 'Content'}
      <section class="admin-panel">
        <h3>Top pages</h3>
        {#if asRows(data?.top_paths).length === 0}
          <p class="admin-muted">No data yet</p>
        {:else}
          <div class="admin-rank">
            {#each asRows(data?.top_paths) as row}
              <button class="admin-rank-row link" type="button" onclick={() => openPath(str(row.path))}>
                <span class="admin-rank-label">{str(row.label ?? row.path)}</span>
                <span class="admin-rank-val">{num(row.views)}</span>
              </button>
            {/each}
          </div>
        {/if}
      </section>

      {@render rankPanel('Sections', asRows(data?.sections), 'section')}

      <section class="admin-panel">
        <h3>Article performance</h3>
        {#if asRows(data?.articles).length === 0}
          <p class="admin-muted">No data yet</p>
        {:else}
          {#each asRows(data?.articles) as row}
            {@const spark = sparklinePath(asRows(row.daily))}
            {@const age = row.age_days == null ? null : num(row.age_days)}
            <button class="admin-score-row linkish" type="button" onclick={() => openPath(str(row.path))}>
              <div class="admin-score-copy">
                <p class="title">{str(row.label ?? row.path)}</p>
                <p class="meta">
                  {#if str(row.section)}{str(row.section)}{/if}
                  {#if age != null}
                    {str(row.section) ? ' · ' : ''}{age <= 0 ? 'today' : age === 1 ? '1d old' : `${age}d old`}
                  {/if}
                </p>
              </div>
              {#if spark}
                <svg class="spark" viewBox="0 0 90 34" width="90" height="34" aria-hidden="true">
                  <polygon points={spark.fill} fill="color-mix(in srgb, var(--primary) 14%, transparent)" />
                  <polyline
                    points={spark.line}
                    fill="none"
                    stroke="var(--primary)"
                    stroke-width="2"
                    stroke-linejoin="round"
                    stroke-linecap="round"
                  />
                </svg>
              {/if}
              <span class="admin-score-views">{num(row.views)}</span>
            </button>
          {/each}
        {/if}
      </section>

      {@render rankPanel('Top searches', asRows(data?.top_searches), 'query')}
      {@render rankPanel('Searches with no results', asRows(data?.zero_searches), 'query')}
      {@render rankPanel('Broken / missing URLs (404)', asRows(data?.top_notfound), 'label')}
    {:else}
      <div class="admin-donut-row">
        {@render donutCard('Devices', devices)}
        {@render donutCard('Browsers', browsers)}
        {@render donutCard('Languages', languages)}
      </div>

      <section class="admin-panel">
        <h3>Visits — new vs returning</h3>
        {#if sessionsStack}
          <div class="admin-legend">
            <span class="admin-legend-item">
              <span class="admin-legend-dot" style="background:{CHART_PALETTE[0]}"></span> New
            </span>
            <span class="admin-legend-item">
              <span class="admin-legend-dot" style="background:{CHART_PALETTE[1]}"></span> Returning
            </span>
          </div>
          {@render stackedChart(sessionsStack)}
        {:else}
          <p class="admin-muted">No data yet</p>
        {/if}
      </section>

      <section class="admin-panel">
        <h3>By hour of day (UTC)</h3>
        {#if hourBars && hourBars.bars.some((b) => b.v > 0)}
          <div class="admin-chart" role="img" aria-label="Views by hour UTC">
            <svg viewBox={`0 0 ${hourBars.w} ${hourBars.h}`}>
              {#each hourBars.ticks as tick}
                <line class="grid" x1={hourBars.padL} x2={hourBars.w - 8} y1={tick.y} y2={tick.y} />
                <text class="axis" x={hourBars.padL - 6} y={tick.y + 3} text-anchor="end">{tick.val}</text>
              {/each}
              {#each hourBars.bars as bar, i}
                <rect fill="var(--primary)" x={bar.x} y={bar.y} width={bar.w} height={bar.h} rx="2">
                  <title>{bar.label}: {bar.v}</title>
                </rect>
                {#if i % 6 === 0}
                  <text
                    class="axis bottom"
                    x={bar.x + bar.w / 2}
                    y={hourBars.padT + hourBars.plotH + 16}
                    text-anchor="middle">{bar.label}</text
                  >
                {/if}
              {/each}
            </svg>
          </div>
        {:else}
          <p class="admin-muted">No data yet</p>
        {/if}
      </section>

      <section class="admin-panel">
        <h3>Countries</h3>
        {#if asRows(data?.geo).length === 0}
          <p class="admin-muted">No data yet</p>
        {:else}
          <div class="admin-rank">
            {#each asRows(data?.geo) as row}
              <div class="admin-rank-row">
                <span class="admin-rank-label"
                  >{flagEmoji(str(row.country))} {str(row.country) || '—'}</span
                >
                <span class="admin-rank-val">{num(row.views)}</span>
              </div>
            {/each}
          </div>
          <p class="geo-note admin-muted">IP geolocation by DB-IP (db-ip.com)</p>
        {/if}
      </section>
    {/if}
  {/if}
</div>

{#snippet donutCard(title: string, pack: ReturnType<typeof donutSlices>)}
  <section class="admin-panel admin-donut">
    <h3>{title}</h3>
    {#if pack.total === 0}
      <p class="admin-muted">No data yet</p>
    {:else}
      {@const paths = donutPaths(pack.slices, pack.total)}
      <div class="admin-donut-body">
        <svg width="110" height="110" viewBox="0 0 110 110" aria-hidden="true">
          {#each paths as p}
            <path d={p.d} fill={p.color} />
          {/each}
        </svg>
        <div class="admin-donut-legend">
          {#each pack.slices as s}
            <div class="admin-donut-legend-row">
              <span class="admin-donut-swatch" style="background:{s.color}"></span>
              <span class="name">{s.label}</span>
              <span class="pct">{pack.total ? Math.round((s.value / pack.total) * 100) : 0}%</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </section>
{/snippet}

{#snippet rankPanel(
  title: string,
  rows: Array<Record<string, unknown>>,
  key: string,
  opts: { external?: boolean } = {},
)}
  <section class="admin-panel">
    <h3>{title}</h3>
    {#if rows.length === 0}
      <p class="admin-muted">No data yet</p>
    {:else}
      <div class="admin-rank">
        {#each rows as row}
          {#if opts.external}
            <button
              class="admin-rank-row link"
              type="button"
              onclick={() => openExternal(str(row[key]))}
            >
              <span class="admin-rank-label">{str(row[key])}</span>
              <span class="admin-rank-val">{num(row.views)}</span>
            </button>
          {:else}
            <div class="admin-rank-row">
              <span class="admin-rank-label">{str(row[key])}</span>
              <span class="admin-rank-val">{num(row.views)}</span>
            </div>
          {/if}
        {/each}
      </div>
    {/if}
  </section>
{/snippet}

{#snippet stackedChart(layout: NonNullable<ReturnType<typeof layoutStackedBars>>)}
  <div class="admin-chart">
    <svg viewBox={`0 0 ${layout.w} ${layout.h}`}>
      {#each layout.ticks as tick}
        <line class="grid" x1={layout.padL} x2={layout.w - 8} y1={tick.y} y2={tick.y} />
        <text class="axis" x={layout.padL - 6} y={tick.y + 3} text-anchor="end">{tick.val}</text>
      {/each}
      {#each layout.columns as col, i}
        {#each col.segments as seg}
          <rect x={col.x} y={seg.y} width={col.w} height={seg.h} fill={seg.color} rx="1">
            <title>{col.label} {seg.key}: {seg.v}</title>
          </rect>
        {/each}
        {#if i % layout.showEvery === 0}
          <text
            class="axis bottom"
            x={col.x + col.w / 2}
            y={layout.padT + layout.plotH + 16}
            text-anchor="middle">{col.label}</text
          >
        {/if}
      {/each}
    </svg>
  </div>
{/snippet}

<style>
  .tools {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .src {
    color: var(--muted);
    font-weight: 600;
  }
  .score-block {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    width: 100%;
    padding: 10px 0;
    border: 0;
    border-bottom: 1px solid var(--border);
    background: transparent;
    text-align: start;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }
  .score-block:last-child {
    border-bottom: 0;
  }
  .score-block .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .ref-group {
    border-bottom: 1px solid var(--border);
    padding: 4px 0;
  }
  .ref-group:last-child {
    border-bottom: 0;
  }
  .ref-group summary {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 0;
    cursor: pointer;
    font-weight: 600;
    list-style: none;
  }
  .ref-group summary::-webkit-details-marker {
    display: none;
  }
  .sample {
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
  }
  .sample:last-child {
    border-bottom: 0;
  }
  .sample-top {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    align-items: center;
    margin-bottom: 4px;
  }
  .sample-top strong {
    font-size: 0.92rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .linkish {
    width: 100%;
    border: 0;
    background: transparent;
    padding: 0;
    font: inherit;
    color: inherit;
    cursor: pointer;
    text-align: start;
  }
  .spark {
    flex-shrink: 0;
  }
  .geo-note {
    margin-top: 8px;
    font-size: 11px;
  }
</style>
