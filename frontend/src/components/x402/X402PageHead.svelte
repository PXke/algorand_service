<script lang="ts">
  /**
   * One page head for every marketplace page: a title, one plain line under
   * it, and (optionally) the page's single action on the right. No kicker,
   * no accent bar, no paragraph of product prose -- the page itself is the
   * explanation.
   */
  import type { Snippet } from 'svelte'

  let {
    title,
    lead = '',
    action,
  }: { title: string; lead?: string; action?: Snippet } = $props()
</script>

<header class="x402-head">
  <div class="text">
    <h1>{title}</h1>
    {#if lead}
      <p class="lead">{lead}</p>
    {/if}
  </div>
  {#if action}
    <div class="action">{@render action()}</div>
  {/if}
</header>

<style>
  .x402-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px 32px;
    flex-wrap: wrap;
  }
  .text {
    flex: 1 1 320px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  h1 {
    margin: 0;
    font-size: clamp(24px, 3vw, 30px);
    line-height: 1.15;
    letter-spacing: -0.3px;
    overflow-wrap: anywhere;
  }
  .lead {
    margin: 0;
    color: var(--muted);
    font-size: 15px;
    line-height: 1.5;
    max-width: 42rem;
  }
  .action {
    flex: 0 0 auto;
    align-self: flex-end;
  }
</style>
