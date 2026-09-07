<script lang="ts">
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import PageMeta from '../components/PageMeta.svelte'
</script>

<PageMeta
  title={t($messages, 'notFound')}
  path={typeof window !== 'undefined' ? window.location.pathname : '/'}
  noindex
/>

<div class="page stack">
  <header class="page-head">
    <span class="accent-slug"></span>
    <h1>{t($messages, 'notFound')}</h1>
  </header>
  <div class="actions">
    <button class="btn btn-primary" type="button" onclick={() => navigate('/')}>
      {t($messages, 'navHome')}
    </button>
  </div>
</div>

<style>
  .actions {
    margin-top: 4px;
  }
</style>
