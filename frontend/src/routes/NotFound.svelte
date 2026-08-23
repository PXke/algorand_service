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
  <span class="accent-slug"></span>
  <h1>{t($messages, 'notFound')}</h1>
  <button class="btn btn-primary" type="button" onclick={() => navigate('/')}>
    {t($messages, 'navHome')}
  </button>
</div>

<style>
  h1 {
    margin: 8px 0 12px;
    font-size: clamp(28px, 4vw, 34px);
  }
</style>
