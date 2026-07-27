<script lang="ts">
  import './admin.css'
  import { messages, t } from '../../lib/i18n'
  import { walletAddress, sessionToken, isAdmin } from '../../lib/auth/session'
  import { createAdminApi } from '../../lib/api/admin'
  import { route, navigate } from '../../lib/router'
  import SeedsTab from './tabs/SeedsTab.svelte'
  import ArticlesTab from './tabs/ArticlesTab.svelte'
  import BriefsTab from './tabs/BriefsTab.svelte'
  import ClassifierTab from './tabs/ClassifierTab.svelte'
  import QueueTab from './tabs/QueueTab.svelte'
  import TrainingTab from './tabs/TrainingTab.svelte'
  import GatekeeperTab from './tabs/GatekeeperTab.svelte'
  import DomainsTab from './tabs/DomainsTab.svelte'
  import ToolInsightsTab from './tabs/ToolInsightsTab.svelte'
  import SessionsTab from './tabs/SessionsTab.svelte'
  import AnalyticsTab from './tabs/AnalyticsTab.svelte'
  import InboxTab from './tabs/InboxTab.svelte'
  import SystemTab from './tabs/SystemTab.svelte'
  import PageMeta from '../../components/PageMeta.svelte'
  import BrandMark from '../../components/BrandMark.svelte'

  const tabs = [
    { id: 'Analytics', label: 'Analytics', slug: 'analytics', group: 'Content' },
    { id: 'Articles', label: 'Articles', slug: 'articles', group: 'Content' },
    { id: 'Writer Briefs', label: 'Briefs', slug: 'briefs', group: 'Content' },
    { id: 'Inbox', label: 'Inbox', slug: 'inbox', group: 'Content' },
    { id: 'Queue', label: 'Queue', slug: 'queue', group: 'Pipeline' },
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

  function tabFromQuery(q: URLSearchParams): TabId {
    const raw = (q.get('tab') || '').trim().toLowerCase()
    return slugToId[raw] ?? 'Analytics'
  }

  let tab = $state<TabId>(tabFromQuery($route.query))
  let walletOpen = $state(false)
  let WalletDialog = $state<import('svelte').Component<{ onclose: () => void }> | null>(null)
  let toast = $state<string | null>(null)

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

    {#key tab}
      {#if tab === 'Seeds'}
        <SeedsTab {admin} onmessage={flash} />
      {:else if tab === 'Articles'}
        <ArticlesTab {admin} onmessage={flash} />
      {:else if tab === 'Writer Briefs'}
        <BriefsTab {admin} onmessage={flash} />
      {:else if tab === 'Classifier'}
        <ClassifierTab {admin} onmessage={flash} />
      {:else if tab === 'Queue'}
        <QueueTab {admin} />
      {:else if tab === 'Training'}
        <TrainingTab {admin} onmessage={flash} />
      {:else if tab === 'Gatekeeper'}
        <GatekeeperTab {admin} onmessage={flash} />
      {:else if tab === 'Domains'}
        <DomainsTab {admin} onmessage={flash} />
      {:else if tab === 'Tool Insights'}
        <ToolInsightsTab {admin} />
      {:else if tab === 'Sessions'}
        <SessionsTab {admin} />
      {:else if tab === 'Analytics'}
        <AnalyticsTab {admin} />
      {:else if tab === 'Inbox'}
        <InboxTab {admin} onmessage={flash} />
      {:else if tab === 'System'}
        <SystemTab {admin} onmessage={flash} />
      {/if}
    {/key}
  {/if}
</div>

{#if walletOpen && WalletDialog}
  <WalletDialog onclose={() => (walletOpen = false)} />
{/if}
