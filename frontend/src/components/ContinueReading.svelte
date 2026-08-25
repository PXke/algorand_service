<script lang="ts">
  import { onMount } from 'svelte'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { clearContinue, readContinue, type ContinueReading } from '../lib/continueReading'

  let entry = $state<ContinueReading | null>(null)

  onMount(() => {
    entry = readContinue()
  })

  // A floor below which "X% left" is just restating "you haven't started" —
  // not worth the extra copy.
  const remainingPct = $derived.by(() => {
    const p = entry?.progress
    if (p == null || p < 0.05) return null
    return Math.round((1 - p) * 100)
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
  <aside class="cont motion-fade-up" aria-label={t($messages, 'continueReading')}>
    <button class="main" type="button" onclick={resume}>
      <span class="copy">
        <span class="label">
          {t($messages, 'continueReading')}
          {#if remainingPct != null}
            <span class="pct">· {t($messages, 'articleRemainingLabel', { pct: remainingPct })}</span>
          {/if}
        </span>
        <strong class="title">{entry.title}</strong>
      </span>
      <span class="go" aria-hidden="true">›</span>
    </button>
    <button class="x" type="button" title={t($messages, 'dismiss')} aria-label={t($messages, 'dismiss')} onclick={dismiss}>
      ×
    </button>
  </aside>
{/if}

<style>
  /* Resume slug above the lead — lighter than a story, no card, no glyphs. */
  .cont {
    display: flex;
    align-items: stretch;
    gap: 4px;
    margin: 0 0 4px;
    padding: 0 0 2px;
    border-bottom: 1px solid var(--border);
    background: transparent;
  }
  :global(.motion-fade-up) {
    animation: fade-up-in 0.32s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  .main {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    border: 0;
    background: transparent;
    text-align: start;
    padding: 8px 0 10px;
    color: inherit;
  }
  .main:hover .title,
  .main:focus-visible .title {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .main:focus-visible {
    outline: none;
  }
  .copy {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .label {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--accent);
  }
  .pct {
    color: var(--muted);
    font-weight: 500;
  }
  .title {
    font-family: var(--font-display);
    font-size: 14px;
    font-weight: 600;
    letter-spacing: -0.15px;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .go {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 18px;
    font-weight: 500;
    color: var(--accent);
    line-height: 1;
  }
  .x {
    border: 0;
    background: transparent;
    color: var(--subtle);
    width: 44px;
    min-height: 44px;
    display: grid;
    place-items: center;
    flex-shrink: 0;
    font-size: 20px;
    line-height: 1;
    font-weight: 400;
  }
  .x:hover,
  .x:focus-visible {
    color: var(--on-surface);
    outline: none;
  }
</style>
