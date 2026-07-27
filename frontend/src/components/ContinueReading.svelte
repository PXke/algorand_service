<script lang="ts">
  import { onMount } from 'svelte'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { clearContinue, readContinue, type ContinueReading } from '../lib/continueReading'
  import Icon from './Icon.svelte'

  let entry = $state<ContinueReading | null>(null)

  onMount(() => {
    entry = readContinue()
  })

  function resume() {
    if (!entry) return
    navigate(entry.path)
  }

  function dismiss() {
    clearContinue()
    entry = null
  }
</script>

{#if entry}
  <aside class="cont" aria-label={t($messages, 'continueReading')}>
    <button class="main" type="button" onclick={resume}>
      <span class="ico" aria-hidden="true"><Icon name="history" size={18} /></span>
      <span class="copy">
        <span class="label">{t($messages, 'continueReading')}</span>
        <strong class="title">{entry.title}</strong>
      </span>
      <span class="go" aria-hidden="true"><Icon name="arrow_forward" size={18} /></span>
    </button>
    <button class="x" type="button" title={t($messages, 'dismiss')} onclick={dismiss}>
      <Icon name="close" size={16} />
    </button>
  </aside>
{/if}

<style>
  /* A resume prompt, not a story: it sits above the lead, so it has to stay
     visibly lighter than one. Ruled strip rather than a filled card. */
  .cont {
    display: flex;
    align-items: stretch;
    gap: 4px;
    padding: 0;
    border: 0;
    border-bottom: 1px solid var(--border);
    border-radius: 0;
    background: transparent;
  }
  .main {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
    border: 0;
    background: transparent;
    text-align: start;
    padding: 6px 2px 10px;
    border-radius: 8px;
    color: inherit;
  }
  .main:hover,
  .main:focus-visible {
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    outline: none;
  }
  .ico,
  .go {
    color: var(--primary);
    display: grid;
    place-items: center;
    flex-shrink: 0;
  }
  .copy {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .label {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  /* Single line: two lines of display serif read as a headline and competed
     with the lead story directly beneath it. */
  .title {
    font-family: var(--font-sans);
    font-size: 14px;
    font-weight: 600;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .x {
    border: 0;
    background: transparent;
    color: var(--subtle);
    width: 44px;
    min-height: 44px;
    border-radius: 10px;
    display: grid;
    place-items: center;
    flex-shrink: 0;
  }
  .x:hover,
  .x:focus-visible {
    color: var(--on-surface);
    background: color-mix(in srgb, var(--on-surface) 8%, transparent);
    outline: none;
  }
  @media (max-width: 519px) {
    .main {
      padding: 8px 2px 10px;
      gap: 10px;
    }
  }
</style>
