<script lang="ts">
  import './admin.css'
  import { messages, t } from '../../lib/i18n'
  import { walletAddress, sessionToken, isAdmin } from '../../lib/auth/session'
  import { createAdminApi } from '../../lib/api/admin'
  import WalletDialog from '../../components/WalletDialog.svelte'
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

  const tabs = [
    { id: 'Analytics', label: 'Analytics' },
    { id: 'Articles', label: 'Articles' },
    { id: 'Queue', label: 'Queue' },
    { id: 'Classifier', label: 'Classifier' },
    { id: 'Domains', label: 'Domains' },
    { id: 'Seeds', label: 'Seeds' },
    { id: 'Writer Briefs', label: 'Briefs' },
    { id: 'Training', label: 'Training' },
    { id: 'Gatekeeper', label: 'Gatekeeper' },
    { id: 'Tool Insights', label: 'Insights' },
    { id: 'Sessions', label: 'Sessions' },
    { id: 'Inbox', label: 'Inbox' },
    { id: 'System', label: 'System' },
  ] as const

  type TabId = (typeof tabs)[number]['id']

  let tab = $state<TabId>('Analytics')
  let walletOpen = $state(false)
  let toast = $state<string | null>(null)

  const admin = $derived(
    $walletAddress && $isAdmin ? createAdminApi($walletAddress, $sessionToken) : null,
  )

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
    <p class="admin-muted">{t($messages, 'adminAccessDenied')}</p>
    <button class="btn btn-primary" type="button" onclick={() => (walletOpen = true)}>
      {t($messages, 'walletConnect')}
    </button>
  {:else if !$isAdmin}
    <p class="admin-muted">{t($messages, 'adminAccessDenied')}</p>
    <p class="admin-muted">{$walletAddress}</p>
  {:else if admin}
    <nav class="admin-tabs" aria-label="Admin sections">
      {#each tabs as item}
        <button type="button" class:active={tab === item.id} onclick={() => (tab = item.id)}>
          {item.label}
        </button>
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

{#if walletOpen}
  <WalletDialog onclose={() => (walletOpen = false)} />
{/if}
