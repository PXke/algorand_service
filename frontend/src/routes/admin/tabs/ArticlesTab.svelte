<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'
  import { newsApi } from '../../../lib/api/news'
  import Markdown from '../../../components/Markdown.svelte'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  let list: Array<Record<string, unknown>> = $state([])
  let selectedId = $state<string | null>(null)
  let title = $state('')
  let summary = $state('')
  let body = $state('')
  let loadingList = $state(true)
  let loadingArticle = $state(false)
  let saving = $state(false)
  let deleting = $state(false)
  let error = $state<string | null>(null)
  let deleteOpen = $state(false)
  let blockSource = $state(false)
  let showPreview = $state(true)

  async function loadList() {
    loadingList = true
    error = null
    try {
      const feed = await newsApi.fetchFeedPage({ limit: 40 })
      list = feed.items as unknown as Array<Record<string, unknown>>
      if (!selectedId && list[0]) await select(String(list[0].article_id))
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loadingList = false
    }
  }

  async function select(id: string) {
    selectedId = id
    loadingArticle = true
    error = null
    try {
      const a = await newsApi.fetchArticle(id)
      title = String(a.title ?? '')
      summary = String(a.summary ?? '')
      body = String(a.body ?? '')
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loadingArticle = false
    }
  }

  async function reloadSelected() {
    if (!selectedId) return
    await select(selectedId)
    onmessage?.('Article reloaded')
  }

  async function save() {
    if (!selectedId) return
    saving = true
    error = null
    try {
      await admin.patchArticle(selectedId, { title, summary, body })
      onmessage?.('Article saved')
      await loadList()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      saving = false
    }
  }

  async function confirmDelete() {
    if (!selectedId) return
    deleting = true
    error = null
    try {
      await admin.deleteArticle(selectedId, blockSource)
      onmessage?.(blockSource ? 'Article deleted, source blocked' : 'Article deleted')
      deleteOpen = false
      blockSource = false
      selectedId = null
      title = ''
      summary = ''
      body = ''
      await loadList()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      deleting = false
    }
  }

  function formatPublished(raw: unknown): string {
    const s = String(raw ?? '')
    return s.includes('T') ? s.split('T')[0] : s
  }

  $effect(() => {
    void loadList()
  })
</script>

<div class="tab">
  <div class="toolbar">
    <h2>Articles</h2>
    <button class="btn" type="button" onclick={() => loadList()}>Refresh list</button>
  </div>

  {#if error}<p class="err">{error}</p>{/if}

  {#if loadingList}
    <p class="muted">Loading feed…</p>
  {:else}
    <div class="panes">
      <aside class="list panel">
        <p class="list-label">Recent articles</p>
        {#each list as a (a.article_id)}
          <button
            type="button"
            class="item"
            class:on={selectedId === a.article_id}
            onclick={() => select(String(a.article_id))}
          >
            <strong>{String(a.title ?? a.article_id)}</strong>
            {#if a.published_at}
              <span class="subtle">{formatPublished(a.published_at)}</span>
            {/if}
          </button>
        {:else}
          <p class="muted list-empty">No articles yet.</p>
        {/each}
      </aside>

      <div class="editor stack panel">
        {#if selectedId}
          <div class="editor-head">
            <span class="article-id mono">{selectedId}</span>
            {#if loadingArticle}
              <span class="loading-badge">Loading body…</span>
            {/if}
          </div>

          {#if loadingArticle}
            <div class="skeleton">
              <div class="sk-line"></div>
              <div class="sk-line short"></div>
              <div class="sk-block"></div>
            </div>
          {:else}
            <label class="field">
              <span>Title</span>
              <input bind:value={title} />
            </label>
            <label class="field">
              <span>Summary (deck)</span>
              <textarea rows="3" bind:value={summary}></textarea>
            </label>
            <div class="field">
              <div class="body-label">
                <span>Body (markdown)</span>
                <button
                  class="preview-toggle"
                  type="button"
                  onclick={() => (showPreview = !showPreview)}
                >
                  {showPreview ? 'Hide preview' : 'Show preview'}
                </button>
              </div>
              <div class="body-split" class:with-preview={showPreview}>
                <textarea rows="14" class="mono-body" bind:value={body}></textarea>
                {#if showPreview}
                  <div class="preview panel">
                    {#if body.trim()}
                      <Markdown source={body} />
                    {:else}
                      <p class="muted">Nothing to preview yet.</p>
                    {/if}
                  </div>
                {/if}
              </div>
            </div>
          {/if}

          <div class="actions">
            <button
              class="btn danger-text"
              type="button"
              disabled={loadingArticle || saving || deleting}
              onclick={() => (deleteOpen = true)}
            >
              Delete
            </button>
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              disabled={loadingArticle}
              onclick={() => reloadSelected()}
            >
              Reload
            </button>
            <button
              class="btn btn-primary"
              type="button"
              disabled={loadingArticle || saving || deleting}
              onclick={() => save()}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        {:else}
          <div class="empty-editor">
            <p class="muted">Pick an article on the left to edit it.</p>
          </div>
        {/if}
      </div>
    </div>
  {/if}
</div>

{#if deleteOpen}
  <div class="overlay" role="dialog" aria-modal="true" aria-labelledby="delete-title">
    <div class="dialog panel stack">
      <h3 id="delete-title">Delete article?</h3>
      <p>
        {#if title.trim()}
          "{title.trim()}" will be removed from the feed permanently.
        {:else}
          This article will be removed from the feed permanently.
        {/if}
      </p>
      <label class="check-row">
        <input type="checkbox" bind:checked={blockSource} />
        <span>
          <strong>Also block this source</strong>
          <span class="subtle">Stop it from ever being re-crawled or re-composed</span>
        </span>
      </label>
      <div class="form-actions">
        <button class="btn" type="button" onclick={() => (deleteOpen = false)}>Cancel</button>
        <button class="btn btn-danger" type="button" disabled={deleting} onclick={() => confirmDelete()}>
          {deleting ? 'Deleting…' : 'Delete'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  h2,
  h3 {
    margin: 0;
  }
  h2 {
    font-size: 1.25rem;
  }
  .panes {
    display: grid;
    gap: 16px;
  }
  @media (min-width: 900px) {
    .panes {
      grid-template-columns: 280px 1fr;
    }
  }
  .list {
    max-height: 70vh;
    overflow: auto;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .list-label {
    margin: 4px 8px 8px;
    font-size: 0.82rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: var(--subtle);
  }
  .list-empty {
    padding: 16px 8px;
    margin: 0;
  }
  .item {
    text-align: start;
    border: 0;
    background: transparent;
    padding: 10px;
    border-radius: 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    width: 100%;
    cursor: pointer;
  }
  .item strong {
    font-size: 0.88rem;
    line-height: 1.35;
  }
  .item.on,
  .item:hover {
    background: var(--accent-soft);
  }
  .editor {
    min-height: 360px;
    padding: 16px;
  }
  .editor-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }
  .article-id {
    font-size: 0.78rem;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .loading-badge {
    font-size: 0.78rem;
    color: var(--primary);
    font-weight: 600;
    flex-shrink: 0;
  }
  .mono-body {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.85rem;
    line-height: 1.55;
    min-height: 280px;
  }
  .body-label {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
  }
  .preview-toggle {
    border: 0;
    background: transparent;
    color: var(--primary);
    font-size: 0.82rem;
    font-weight: 700;
    padding: 0;
    cursor: pointer;
  }
  .body-split {
    display: grid;
    gap: 12px;
  }
  @media (min-width: 1100px) {
    .body-split.with-preview {
      grid-template-columns: 1fr 1fr;
      align-items: stretch;
    }
  }
  .preview {
    max-height: 420px;
    overflow: auto;
    padding: 14px 16px;
    font-size: 0.92rem;
  }
  .skeleton {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 8px 0 20px;
  }
  .sk-line,
  .sk-block {
    background: var(--surface);
    border-radius: 6px;
    animation: pulse 1.2s ease-in-out infinite;
  }
  .sk-line {
    height: 14px;
    width: 70%;
  }
  .sk-line.short {
    width: 40%;
  }
  .sk-block {
    height: 200px;
  }
  @keyframes pulse {
    0%,
    100% {
      opacity: 0.55;
    }
    50% {
      opacity: 1;
    }
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-top: 4px;
  }
  .spacer {
    flex: 1;
  }
  .danger-text {
    color: var(--danger);
  }
  .btn-danger {
    background: var(--danger);
    color: #fff;
    border-color: var(--danger);
  }
  .empty-editor {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 240px;
  }
  .check-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    cursor: pointer;
  }
  .check-row span {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 0.92rem;
  }
  .form-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  .overlay {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, var(--on-surface) 45%, transparent);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 200;
  }
  .dialog {
    width: min(440px, 100%);
  }
  .dialog p {
    margin: 0;
    line-height: 1.45;
  }
  .err {
    color: var(--danger);
    margin: 0 0 12px;
  }
</style>
