<script lang="ts">
  // Owner-supplied article sources (docs/newspaper-article-sources-design.md,
  // Phase 1) -- attach is storage-only, no Celery dispatch; the article's own
  // Recompose action loads whatever's active here on its next run. Extracted
  // from ClassifierTab.svelte (2026-09-09) so ArticlesTab can offer the same
  // panel on a DRAFT article -- the backend/worker read path was already
  // status-agnostic (recompose_published's load_active_sources has no status
  // check either), the attach UI just never existed anywhere but the
  // Classifier (on-hold) tab, which is a real gap for the "park it in draft,
  // ask the subject a question, attach the reply, recompose" workflow: a
  // source could not be attached once an article left the review queue.
  import type { AdminApi } from '../lib/api/admin'
  import { LatestOnly } from '../lib/asyncGuard'

  type SourceItem = Record<string, unknown>

  let {
    admin,
    articleId,
    onSourcesChange = undefined,
  }: {
    admin: AdminApi
    articleId: string
    onSourcesChange?: (count: number) => void
  } = $props()

  let sourcesOpen = $state(false)
  let sourcesLoading = $state(false)
  let sourcesError = $state<string | null>(null)
  let sources = $state<SourceItem[]>([])
  let newSourceLabel = $state('')
  let newSourceContent = $state('')
  let attaching = $state(false)
  let attachNudge = $state(false)
  let removingSourceId = $state<string | null>(null)
  const sourcesInflight = new LatestOnly()

  async function loadSources(id: string) {
    const { signal, stale } = sourcesInflight.next()
    sourcesLoading = true
    sourcesError = null
    try {
      const res = await admin.listArticleSources(id, signal)
      if (stale()) return
      sources = Array.isArray(res.items) ? (res.items as SourceItem[]) : []
      onSourcesChange?.(sources.length)
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      sourcesError = e instanceof Error ? e.message : String(e)
    } finally {
      if (!stale()) sourcesLoading = false
    }
  }

  function toggleSources() {
    sourcesOpen = !sourcesOpen
  }

  async function attachSource() {
    const id = articleId
    const label = newSourceLabel.trim()
    const content = newSourceContent.trim()
    if (!id || !label || !content || attaching) return
    attaching = true
    attachNudge = false
    sourcesError = null
    try {
      await admin.createArticleSource(id, { label, content })
      newSourceLabel = ''
      newSourceContent = ''
      attachNudge = true
      await loadSources(id)
    } catch (e) {
      sourcesError = e instanceof Error ? e.message : String(e)
    } finally {
      attaching = false
    }
  }

  async function removeSource(sourceId: string) {
    const id = articleId
    if (!id || !sourceId || removingSourceId != null) return
    removingSourceId = sourceId
    sourcesError = null
    try {
      await admin.deleteArticleSource(id, sourceId)
      await loadSources(id)
    } catch (e) {
      sourcesError = e instanceof Error ? e.message : String(e)
    } finally {
      removingSourceId = null
    }
  }

  function formatSourceDate(epoch: unknown): string {
    const n = Number(epoch)
    if (!n) return ''
    return new Date(n * 1000).toLocaleDateString()
  }

  $effect(() => {
    const id = articleId
    if (!id) {
      sources = []
      sourcesError = null
      onSourcesChange?.(0)
      return
    }
    void loadSources(id)
  })
</script>

{#if articleId}
  <section class="owner-sources">
    <button
      type="button"
      class="sources-toggle"
      aria-expanded={sourcesOpen}
      onclick={() => toggleSources()}
    >
      <strong>Owner sources ({sources.length})</strong>
      <span class="admin-muted">Exclusive material handed to the writer on the next recompose</span>
    </button>
    {#if sourcesOpen}
      <div class="sources-body">
        {#if sourcesLoading && sources.length === 0}
          <p class="admin-muted">Loading sources…</p>
        {/if}
        {#if sourcesError}
          <p class="admin-err">{sourcesError}</p>
        {/if}
        {#if sources.length > 0}
          <ul class="source-list">
            {#each sources as source (String(source.source_id))}
              {@const sourceId = String(source.source_id ?? '')}
              <li class="source-item">
                <div class="source-item-head">
                  <strong>{String(source.label ?? '')}</strong>
                  <span class="admin-muted">{formatSourceDate(source.added_at_epoch)}</span>
                </div>
                <p class="source-preview">{String(source.content_preview ?? '')}</p>
                <button
                  class="btn btn-danger"
                  type="button"
                  disabled={removingSourceId != null}
                  onclick={() => removeSource(sourceId)}
                >
                  {removingSourceId === sourceId ? 'Removing…' : 'Remove'}
                </button>
              </li>
            {/each}
          </ul>
        {:else if !sourcesLoading}
          <p class="admin-muted">No owner-supplied sources attached yet.</p>
        {/if}

        <form
          class="attach-form"
          onsubmit={(e) => {
            e.preventDefault()
            if (!attaching) void attachSource()
          }}
        >
          <label class="field">
            <span class="admin-muted">Label</span>
            <input
              bind:value={newSourceLabel}
              placeholder="Exclusive interview with the founder, 2026-09-02"
              maxlength="200"
              required
            />
          </label>
          <label class="field">
            <span class="admin-muted">Content</span>
            <textarea
              bind:value={newSourceContent}
              rows="6"
              placeholder="Paste the transcript, data, or other material here"
              required
            ></textarea>
          </label>
          <div class="attach-row">
            <button class="btn btn-primary" type="submit" disabled={attaching}>
              {attaching ? 'Attaching…' : 'Attach'}
            </button>
            {#if attachNudge}
              <span class="attach-nudge">Source attached — Recompose when ready</span>
            {/if}
          </div>
        </form>
      </div>
    {/if}
  </section>
{/if}

<style>
  .owner-sources {
    border-top: 1px solid var(--border);
    padding-top: 8px;
  }

  .sources-toggle {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font: inherit;
    color: inherit;
    font-weight: 600;
    font-size: 0.92rem;
  }

  .sources-body {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: 10px;
  }

  .source-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .source-item {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--primary) 5%, var(--panel));
    border: 1px solid var(--border);
  }

  .source-item-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }

  .source-preview {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--muted);
    white-space: pre-wrap;
  }

  .attach-form {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-top: 4px;
  }

  .attach-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }

  .attach-nudge {
    font-size: 12px;
    color: var(--gain);
  }
</style>
