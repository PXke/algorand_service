<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  let items: Array<Record<string, unknown>> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  async function load() {
    loading = true
    error = null
    try {
      const res = await admin.listContactMessages()
      items = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  function copyEmail(email: string) {
    void navigator.clipboard.writeText(email)
    onmessage?.(`Copied ${email}`)
  }

  function displayName(raw: unknown): string {
    const name = String(raw ?? '').trim()
    return name || 'Anonymous reader'
  }

  function formatWhen(epoch: unknown): string {
    const n = Number(epoch)
    if (!n) return ''
    return new Date(n * 1000).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  $effect(() => {
    void load()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <p class="intro">
      Messages sent through the public contact form — newest first, last two months.
    </p>
    <button class="btn" type="button" onclick={() => load()}>Refresh</button>
  </div>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !items.length}
    <div class="empty panel">
      <p class="muted">No messages yet.</p>
    </div>
  {:else}
    {#each items as m (m.created_at_epoch)}
      <article class="panel msg">
        <div class="head">
          <strong>{displayName(m.name)}</strong>
          {#if m.created_at_epoch}
            <time class="subtle">{formatWhen(m.created_at_epoch)}</time>
          {/if}
        </div>
        {#if m.email}
          <button class="email linkish" type="button" onclick={() => copyEmail(String(m.email))}>
            {String(m.email)}
          </button>
        {/if}
        <p class="message selectable">{String(m.message ?? '')}</p>
      </article>
    {/each}
  {/if}
</div>

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }
  .intro {
    margin: 0;
    flex: 1;
    font-size: 0.88rem;
    color: var(--muted);
    line-height: 1.45;
    max-width: 52ch;
  }
  .empty {
    text-align: center;
    padding: 32px 20px;
  }
  .empty p {
    margin: 0;
  }
  .msg {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: baseline;
    gap: 10px;
  }
  .head strong {
    font-size: 0.95rem;
  }
  .email {
    align-self: flex-start;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.85rem;
  }
  .linkish {
    border: 0;
    background: none;
    color: var(--primary);
    font-weight: 600;
    padding: 0;
    cursor: pointer;
  }
  .linkish:hover {
    text-decoration: underline;
  }
  .message {
    margin: 0;
    white-space: pre-wrap;
    line-height: 1.5;
    font-size: 0.92rem;
  }
  .selectable {
    user-select: text;
    -webkit-user-select: text;
  }
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
