<script lang="ts">
  /**
   * One code panel with a row of tabs (curl / Python) and a copy button.
   * The code is rendered as text inside a <pre>, never as markup, so it
   * needs no sanitizer.
   */
  import { messages, t } from '../../lib/i18n'

  let {
    tabs,
    label = '',
  }: { tabs: { key: string; label: string; code: string }[]; label?: string } = $props()

  let activeKey = $state('')
  const active = $derived(tabs.find((x) => x.key === activeKey) ?? tabs[0])

  let copied = $state(false)
  let copyTimer: number | undefined

  async function copy() {
    if (!active) return
    try {
      await navigator.clipboard.writeText(active.code)
      copied = true
      window.clearTimeout(copyTimer)
      copyTimer = window.setTimeout(() => (copied = false), 1600)
    } catch {
      copied = false
    }
  }

  $effect(() => () => window.clearTimeout(copyTimer))
</script>

<div class="code-tabs" role="group" aria-label={label || undefined}>
  <div class="bar">
    <div class="tabs" role="tablist">
      {#each tabs as tab (tab.key)}
        <button
          type="button"
          role="tab"
          class="tab"
          class:active={tab.key === active?.key}
          aria-selected={tab.key === active?.key}
          onclick={() => (activeKey = tab.key)}
        >
          {tab.label}
        </button>
      {/each}
    </div>
    <button type="button" class="copy" onclick={() => void copy()}>
      {copied ? t($messages, 'x402Copied') : t($messages, 'x402Copy')}
    </button>
  </div>
  {#if active}
    <pre class="x402-curl">{active.code}</pre>
  {/if}
</div>

<style>
  .code-tabs {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .bar {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 12px;
  }
  .tabs {
    display: flex;
    gap: 2px;
  }
  .tab {
    appearance: none;
    border: 1px solid transparent;
    border-bottom: 0;
    background: none;
    padding: 6px 12px;
    font-family: var(--font-mono);
    font-size: 12.5px;
    color: var(--muted);
    cursor: pointer;
  }
  .tab.active {
    color: var(--on-surface);
    background: var(--panel);
    border-color: var(--border);
    border-inline-start-color: var(--x402-accent);
    border-inline-start-width: 3px;
    margin-bottom: -1px;
  }
  .tab:focus-visible,
  .copy:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .copy {
    appearance: none;
    border: 0;
    background: none;
    padding: 6px 4px;
    font-family: var(--font-sans);
    font-size: 12.5px;
    color: var(--accent);
    cursor: pointer;
  }
  .copy:hover {
    text-decoration: underline;
  }
  .code-tabs :global(.x402-curl) {
    border-radius: 0;
  }
</style>
