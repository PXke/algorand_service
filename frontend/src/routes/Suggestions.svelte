<script lang="ts">
  import { onMount } from 'svelte'
  import { config } from '../lib/config'
  import { messages, t } from '../lib/i18n'
  import { sessionToken } from '../lib/auth/session'
  import { suggestionsApi } from '../lib/api/suggestions'
  import { navigate } from '../lib/router'
  import { ApiException } from '../lib/api/client'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'

  let items: Array<Record<string, unknown>> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  onMount(() => {
    if (!config.suggestionsEnabled) {
      navigate('/', true)
      return
    }
    void (async () => {
      try {
        const res = await suggestionsApi.list($sessionToken)
        items = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
      } catch (e) {
        error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      } finally {
        loading = false
      }
    })()
  })
</script>

<PageMeta
  title={t($messages, 'pageTitleSuggestions')}
  description={t($messages, 'suggestionsSubtitle') || SITE_TAGLINE}
  path="/suggestions"
  noindex
/>

<div class="page stack">
  <header>
    <span class="accent-slug"></span>
    <h1>{t($messages, 'suggestionsTitle')}</h1>
    <p class="lead muted">{t($messages, 'suggestionsSubtitle')}</p>
  </header>
  {#if !config.suggestionsEnabled}
    <p class="muted">{t($messages, 'suggestionsDisabled')}</p>
  {:else if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else}
    <div class="stack">
      {#each items as item}
        <article class="panel">
          <h3>{String(item.title ?? '')}</h3>
          <p class="muted">{String(item.body ?? '')}</p>
          <p class="subtle">{Number(item.upvote_count ?? 0)} upvotes</p>
        </article>
      {/each}
    </div>
  {/if}
</div>

<style>
  h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  h3 {
    margin: 0;
  }
  .lead {
    margin: 8px 0 0;
    max-width: 42rem;
  }
  .err {
    color: var(--danger);
  }
</style>
