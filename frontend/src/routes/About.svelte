<script lang="ts">
  import { messages, t } from '../lib/i18n'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'

  const blocks = $derived([
    { heading: t($messages, 'aboutHowHeading'), body: t($messages, 'aboutHowBody') },
    { heading: t($messages, 'aboutAiHeading'), body: t($messages, 'aboutAiBody') },
    { heading: t($messages, 'aboutProvenanceHeading'), body: t($messages, 'aboutProvenanceBody') },
    { heading: t($messages, 'aboutStandardsHeading'), body: t($messages, 'aboutStandardsBody') },
  ])
</script>

<PageMeta
  title={t($messages, 'aboutTitle')}
  description={t($messages, 'aboutLead') || SITE_TAGLINE}
  path="/about"
/>

<div class="page page-reading stack about">
  <header>
    <span class="accent-slug"></span>
    <h1>{t($messages, 'aboutTitle')}</h1>
    <p class="lead">{t($messages, 'aboutLead')}</p>
  </header>

  <hr class="hairline" />

  {#each blocks as block (block.heading)}
    <section class="block">
      <h2>{block.heading}</h2>
      <p>{block.body}</p>
    </section>
  {/each}
</div>

<style>
  .about {
    gap: 0;
  }
  header {
    margin-bottom: 8px;
  }
  h1 {
    margin: 14px 0 0;
    font-size: clamp(28px, 4vw, 34px);
    line-height: 1.15;
  }
  .lead {
    margin: 18px 0 0;
    font-family: var(--font-serif);
    font-size: 19px;
    line-height: 1.6;
    color: var(--muted);
  }
  .block {
    margin-top: 28px;
  }
  h2 {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  h2::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 9px;
    background: var(--accent);
    vertical-align: 6%;
  }
  .block p {
    margin: 10px 0 0;
    font-family: var(--font-serif);
    font-size: 17px;
    line-height: 1.65;
    color: var(--body);
  }
</style>
