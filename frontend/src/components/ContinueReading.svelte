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
  .cont {
    display: flex;
    align-items: stretch;
    gap: 4px;
    padding: 4px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background:
      linear-gradient(
        120deg,
        color-mix(in srgb, var(--accent) 10%, var(--panel)) 0%,
        var(--panel) 55%
      );
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
    padding: 10px 12px;
    border-radius: 10px;
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
  .title {
    font-family: var(--font-display);
    font-size: 15px;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .x {
    border: 0;
    background: transparent;
    color: var(--subtle);
    width: 36px;
    border-radius: 10px;
    display: grid;
    place-items: center;
  }
  .x:hover,
  .x:focus-visible {
    color: var(--on-surface);
    background: color-mix(in srgb, var(--on-surface) 8%, transparent);
    outline: none;
  }
</style>
