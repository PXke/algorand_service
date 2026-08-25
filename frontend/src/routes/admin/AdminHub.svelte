<script lang="ts">
  import './admin.css'
  import type { Component } from 'svelte'
  import { messages, t } from '../../lib/i18n'
  import { walletAddress, sessionToken, isAdmin } from '../../lib/auth/session'
  import { createAdminApi } from '../../lib/api/admin'
  import { route, navigate } from '../../lib/router'
  import PageMeta from '../../components/PageMeta.svelte'
  import BrandMark from '../../components/BrandMark.svelte'

  const tabs = [
    { id: 'Analytics', label: 'Analytics', slug: 'analytics', group: 'Content' },
    { id: 'Articles', label: 'Articles', slug: 'articles', group: 'Content' },
    { id: 'Writer Briefs', label: 'Briefs', slug: 'briefs', group: 'Content' },
    { id: 'Inbox', label: 'Inbox', slug: 'inbox', group: 'Content' },
    { id: 'Glossary', label: 'Glossary', slug: 'glossary', group: 'Content' },
    { id: 'Queue', label: 'Queue', slug: 'queue', group: 'Pipeline' },
    {
      id: 'Artifacts Preview',
      label: 'Artifacts (Preview)',
      slug: 'artifacts-preview',
      group: 'Pipeline',
    },
    { id: 'Classifier', label: 'Classifier', slug: 'classifier', group: 'Pipeline' },
    { id: 'Training', label: 'Training', slug: 'training', group: 'Pipeline' },
    { id: 'Gatekeeper', label: 'Gatekeeper', slug: 'gatekeeper', group: 'Pipeline' },
    { id: 'Domains', label: 'Domains', slug: 'domains', group: 'Sources' },
    { id: 'Seeds', label: 'Seeds', slug: 'seeds', group: 'Sources' },
    { id: 'Tool Insights', label: 'Insights', slug: 'insights', group: 'System' },
    { id: 'Sessions', label: 'Sessions', slug: 'sessions', group: 'System' },
    { id: 'System', label: 'System', slug: 'system', group: 'System' },
  ] as const

  type TabId = (typeof tabs)[number]['id']
  type GroupName = (typeof tabs)[number]['group']

  const groups: GroupName[] = ['Content', 'Pipeline', 'Sources', 'System']

  const slugToId = Object.fromEntries(tabs.map((x) => [x.slug, x.id])) as Record<string, TabId>
  const idToSlug = Object.fromEntries(tabs.map((x) => [x.id, x.slug])) as Record<TabId, string>

  /** Tabs that take an onmessage flash callback. */
  const withFlash = new Set<TabId>([
    'Seeds',
    'Articles',
    'Writer Briefs',
    'Glossary',
    'Classifier',
    'Training',
    'Domains',
    'Inbox',
    'System',
    'Artifacts Preview',
  ])

  const tabLoaders: Record<TabId, () => Promise<{ default: Component<any> }>> = {
    Analytics: () => import('./tabs/AnalyticsTab.svelte'),
    Articles: () => import('./tabs/ArticlesTab.svelte'),
    'Writer Briefs': () => import('./tabs/BriefsTab.svelte'),
    Inbox: () => import('./tabs/InboxTab.svelte'),
    Glossary: () => import('./tabs/GlossaryTab.svelte'),
    Queue: () => import('./tabs/QueueTab.svelte'),
    'Artifacts Preview': () => import('./tabs/ArtifactsPreviewTab.svelte'),
    Classifier: () => import('./tabs/ClassifierTab.svelte'),
    Training: () => import('./tabs/TrainingTab.svelte'),
    Gatekeeper: () => import('./tabs/GatekeeperTab.svelte'),
    Domains: () => import('./tabs/DomainsTab.svelte'),
    Seeds: () => import('./tabs/SeedsTab.svelte'),
    'Tool Insights': () => import('./tabs/ToolInsightsTab.svelte'),
    Sessions: () => import('./tabs/SessionsTab.svelte'),
    System: () => import('./tabs/SystemTab.svelte'),
  }

  function tabFromQuery(q: URLSearchParams): TabId {
    const raw = (q.get('tab') || '').trim().toLowerCase()
    return slugToId[raw] ?? 'Analytics'
  }

  let tab = $state<TabId>(tabFromQuery($route.query))
  let walletOpen = $state(false)
  let WalletDialog = $state<import('svelte').Component<{ onclose: () => void }> | null>(null)
  let toast = $state<string | null>(null)
  let ActiveTab = $state<Component<any> | null>(null)
  let tabLoading = $state(false)

  $effect(() => {
    const next = tabFromQuery($route.query)
    if (next !== tab) tab = next
  })

  $effect(() => {
    if (!walletOpen || WalletDialog) return
    void import('../../components/WalletDialog.svelte').then((m) => {
      WalletDialog = m.default
    })
  })

  $effect(() => {
    const id = tab
    let cancelled = false
    tabLoading = true
    ActiveTab = null
    void tabLoaders[id]()
      .then((m) => {
        if (cancelled) return
        ActiveTab = m.default
        tabLoading = false
      })
      .catch(() => {
        if (!cancelled) tabLoading = false
      })
    return () => {
      cancelled = true
    }
  })

  const admin = $derived(
    $walletAddress && $isAdmin ? createAdminApi($walletAddress, $sessionToken) : null,
  )

  function selectTab(id: TabId) {
    tab = id
    const slug = idToSlug[id]
    const next = slug === 'analytics' ? '/admin' : `/admin?tab=${slug}`
    navigate(next, true, false)
  }

  function flash(msg: string) {
    toast = msg
    setTimeout(() => {
      if (toast === msg) toast = null
    }, 2800)
  }

  function shortAddr(a: string) {
    return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
  }
</script>

<PageMeta title={t($messages, 'pageTitleAdmin')} path="/admin" />

<div class="admin-page">
  <header class="admin-head">
    <div>
      <span class="accent-slug"></span>
      <h1>{t($messages, 'adminTitle')}</h1>
    </div>
    {#if $walletAddress}
      <span class="admin-wallet">{shortAddr($walletAddress)}</span>
    {/if}
  </header>

  {#if !$walletAddress}
    <div class="admin-gate panel">
      <BrandMark size={44} />
      <div class="gate-copy">
        <h2>{t($messages, 'adminGateTitle')}</h2>
        <p class="admin-muted">{t($messages, 'adminGateBody')}</p>
      </div>
      <button class="btn btn-primary" type="button" onclick={() => (walletOpen = true)}>
        {t($messages, 'walletConnect')}
      </button>
    </div>
  {:else if !$isAdmin}
    <div class="admin-gate panel">
      <BrandMark size={44} />
      <div class="gate-copy">
        <h2>{t($messages, 'adminAccessDenied')}</h2>
        <p class="admin-muted mono">{$walletAddress}</p>
      </div>
    </div>
  {:else if admin}
    <nav class="admin-nav" aria-label="Admin sections">
      {#each groups as group}
        <div class="admin-group">
          <p class="admin-group-label">{group}</p>
          <div class="admin-tabs">
            {#each tabs.filter((x) => x.group === group) as item}
              <button
                type="button"
                class:active={tab === item.id}
                onclick={() => selectTab(item.id)}
              >
                {item.label}
              </button>
            {/each}
          </div>
        </div>
      {/each}
    </nav>

    {#if toast}<p class="admin-toast">{toast}</p>{/if}

    {#if tabLoading && !ActiveTab}
      <p class="admin-muted">Loading…</p>
    {:else if ActiveTab}
      {#key tab}
        {#if withFlash.has(tab)}
          <ActiveTab {admin} onmessage={flash} />
        {:else}
          <ActiveTab {admin} />
        {/if}
      {/key}
    {/if}
  {/if}
</div>

{#if walletOpen && WalletDialog}
  <WalletDialog onclose={() => (walletOpen = false)} />
{/if}
