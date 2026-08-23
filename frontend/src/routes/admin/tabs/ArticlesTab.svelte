<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'
  import { newsApi } from '../../../lib/api/news'
  import AnnotatedMarkdown from '../../../components/AnnotatedMarkdown.svelte'
  import { diffLines, toLines, type DiffOp } from '../../../lib/diff'
  import type { CommentItem } from '../../../lib/api/sharing'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  let list: Array<Record<string, unknown>> = $state([])
  let draftList: Array<Record<string, unknown>> = $state([])
  let showingDrafts = $state(false)
  let nextCursor = $state<string | null>(null)
  let listEl = $state<HTMLElement | null>(null)
  let sentinel = $state<HTMLElement | null>(null)
  let selectedId = $state<string | null>(null)
  let title = $state('')
  let summary = $state('')
  let body = $state('')
  let isDraft = $state(false)
  let loadingList = $state(true)
  let loadingMore = $state(false)
  let loadingArticle = $state(false)
  let saving = $state(false)
  let deleting = $state(false)
  let recomposing = $state(false)
  let togglingDraft = $state(false)
  let error = $state<string | null>(null)
  let deleteOpen = $state(false)
  let blockSource = $state(false)
  let comments: CommentItem[] = $state([])
  let shareLinks: Array<Record<string, unknown>> = $state([])
  let creatingShareLink = $state(false)
  let newShareLinkLabel = $state('')
  let copiedToken = $state<string | null>(null)
  let showPreview = $state(true)

  type VersionSummary = {
    version: number
    title: string
    edit_reason: string
    editor: string
    edited_at: string | null
  }
  const CURRENT_VERSION = -1 // synthetic id for "the live content shown in the editor now"
  let historyOpen = $state(false)
  let loadingHistory = $state(false)
  let versions = $state<VersionSummary[]>([])
  let diffFromVersion = $state<number | null>(null)
  let diffToVersion = $state<number>(CURRENT_VERSION)
  let diffOps = $state<DiffOp[] | null>(null)
  let loadingDiff = $state(false)
  let diffError = $state<string | null>(null)

  async function loadList() {
    loadingList = true
    error = null
    try {
      const feed = await newsApi.fetchFeedPage({ limit: 40 })
      list = feed.items as unknown as Array<Record<string, unknown>>
      nextCursor = feed.next_cursor
      if (!selectedId && list[0]) await select(String(list[0].article_id))
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loadingList = false
    }
  }

  async function loadDrafts() {
    loadingList = true
    error = null
    try {
      const res = (await admin.listDraftArticles()) as { items: Array<Record<string, unknown>> }
      draftList = res.items ?? []
      if (draftList[0]) {
        try {
          await select(String(draftList[0].article_id))
        } catch (e) {
          error = e instanceof Error ? e.message : String(e)
        }
      } else {
        selectedId = null
        title = ''
        summary = ''
        body = ''
      }
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loadingList = false
    }
  }

  async function toggleView() {
    showingDrafts = !showingDrafts
    selectedId = null
    if (showingDrafts) await loadDrafts()
    else await loadList()
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return
    loadingMore = true
    error = null
    try {
      const feed = await newsApi.fetchFeedPage({ limit: 40, cursor: nextCursor })
      list = [...list, ...(feed.items as unknown as Array<Record<string, unknown>>)]
      nextCursor = feed.next_cursor
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loadingMore = false
    }
  }

  async function select(id: string) {
    selectedId = id
    loadingArticle = true
    error = null
    comments = []
    shareLinks = []
    try {
      // The admin-authenticated read, not newsApi.fetchArticle: that one is
      // the public endpoint, which 404s a drafted article by design.
      const a = (await admin.getArticle(id)) as Record<string, unknown>
      title = String(a.title ?? '')
      summary = String(a.summary ?? '')
      body = String(a.body ?? '')
      isDraft = Boolean(a.draft)
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loadingArticle = false
    }
    void loadCommentsAndShareLinks(id)
  }

  async function loadCommentsAndShareLinks(id: string) {
    try {
      const [commentsResp, linksResp] = await Promise.all([
        admin.listArticleComments(id),
        admin.listShareLinks(id),
      ])
      if (selectedId !== id) return
      comments = ((commentsResp as { items?: CommentItem[] }).items ?? []) as CommentItem[]
      shareLinks = ((linksResp as { items?: Array<Record<string, unknown>> }).items ??
        []) as Array<Record<string, unknown>>
    } catch {
      // Non-fatal — the article itself already loaded (or failed) above.
    }
  }

  async function createShareLink() {
    if (!selectedId) return
    creatingShareLink = true
    try {
      await admin.createShareLink(selectedId, newShareLinkLabel.trim())
      newShareLinkLabel = ''
      await loadCommentsAndShareLinks(selectedId)
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      creatingShareLink = false
    }
  }

  async function revokeShareLink(token: string) {
    if (!selectedId) return
    try {
      await admin.revokeShareLink(selectedId, token)
      await loadCommentsAndShareLinks(selectedId)
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function copyShareUrl(token: string) {
    const url = `${window.location.origin}/shared/${token}`
    try {
      await navigator.clipboard.writeText(url)
      copiedToken = token
      setTimeout(() => {
        if (copiedToken === token) copiedToken = null
      }, 2000)
    } catch {
      /* clipboard unavailable — the link is still shown as text */
    }
  }

  async function deleteComment(commentId: string) {
    if (!selectedId) return
    try {
      await admin.deleteComment(selectedId, commentId)
      comments = comments.filter((c) => c.comment_id !== commentId)
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
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

  async function recomposeSelected() {
    if (!selectedId) return
    if (
      !confirm(
        'Recompose this article now? This spends real usage and can take several minutes ' +
          '(longer for a special edition). The live article updates in place once it finishes.',
      )
    ) {
      return
    }
    recomposing = true
    error = null
    try {
      await admin.recomposeArticle(selectedId)
      onmessage?.('Recompose triggered — check back in a few minutes')
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      recomposing = false
    }
  }

  async function toggleDraft() {
    if (!selectedId) return
    const next = !isDraft
    if (
      next &&
      !confirm(
        'Move this article to draft? It disappears from the public site (404) immediately, ' +
          'but stays intact and can be restored at any time.',
      )
    ) {
      return
    }
    togglingDraft = true
    error = null
    try {
      await admin.setArticleDraft(selectedId, next)
      isDraft = next
      onmessage?.(next ? 'Article moved to draft' : 'Article restored')
      if (showingDrafts) await loadDrafts()
      else await loadList()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      togglingDraft = false
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

  async function openHistory() {
    if (!selectedId) return
    historyOpen = true
    loadingHistory = true
    diffError = null
    diffOps = null
    try {
      const res = (await admin.listArticleVersions(selectedId)) as { items: VersionSummary[] }
      versions = res.items ?? []
      // Default comparison: the most recent saved version vs. the current
      // live content -- the single most useful "what changed last" view.
      diffToVersion = CURRENT_VERSION
      diffFromVersion = versions[0]?.version ?? null
      await runDiff()
    } catch (e) {
      diffError = e instanceof Error ? e.message : String(e)
    } finally {
      loadingHistory = false
    }
  }

  function closeHistory() {
    historyOpen = false
    versions = []
    diffOps = null
    diffError = null
  }

  async function fetchVersionText(
    v: number,
  ): Promise<{ title: string; body: string } | null> {
    if (v === CURRENT_VERSION) return { title, body }
    if (!selectedId) return null
    const res = (await admin.getArticleVersion(selectedId, v)) as {
      title: string
      body: string
    }
    return { title: res.title, body: res.body }
  }

  async function runDiff() {
    if (diffFromVersion === null) {
      diffOps = null
      return
    }
    loadingDiff = true
    diffError = null
    try {
      const [from, to] = await Promise.all([
        fetchVersionText(diffFromVersion),
        fetchVersionText(diffToVersion),
      ])
      if (!from || !to) {
        diffError = 'Could not load one of the selected versions.'
        diffOps = null
        return
      }
      diffOps = diffLines(toLines(from.body), toLines(to.body))
    } catch (e) {
      diffError = e instanceof Error ? e.message : String(e)
      diffOps = null
    } finally {
      loadingDiff = false
    }
  }

  const diffStats = $derived.by(() => {
    if (!diffOps) return { added: 0, removed: 0 }
    let added = 0
    let removed = 0
    for (const op of diffOps) {
      if (op.kind === 'add') added++
      else if (op.kind === 'remove') removed++
    }
    return { added, removed }
  })

  $effect(() => {
    void loadList()
  })

  // Infinite scroll: the sentinel only exists in the DOM while nextCursor is
  // set (see the {#if nextCursor} block below), so this effect re-runs and
  // tears down/reattaches the observer as pages load — no separate cleanup
  // needed once the list is exhausted, the element (and its observer) is
  // simply gone.
  $effect(() => {
    if (!sentinel || !listEl) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) void loadMore()
      },
      { root: listEl, rootMargin: '120px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  })
</script>

<div class="tab">
  <div class="toolbar">
    <h2>Articles</h2>
    <div class="toolbar-actions">
      <button class="btn" type="button" class:on={showingDrafts} onclick={() => toggleView()}>
        {showingDrafts ? 'Show live' : 'Show drafts'}
      </button>
      <button
        class="btn"
        type="button"
        onclick={() => (showingDrafts ? loadDrafts() : loadList())}
      >
        Refresh list
      </button>
    </div>
  </div>

  {#if error}<p class="err">{error}</p>{/if}

  {#if loadingList}
    <p class="muted">Loading feed…</p>
  {:else}
    <div class="panes">
      <aside class="list panel" bind:this={listEl}>
        <p class="list-label">{showingDrafts ? 'Drafted articles' : 'Recent articles'}</p>
        {#each (showingDrafts ? draftList : list) as a (a.article_id)}
          <button
            type="button"
            class="item"
            class:on={selectedId === a.article_id}
            onclick={() => select(String(a.article_id))}
          >
            <strong>{String(a.title ?? a.article_id)}</strong>
            {#if showingDrafts && a.drafted_at}
              <span class="subtle">Drafted {formatPublished(a.drafted_at)}</span>
            {:else if a.published_at}
              <span class="subtle">{formatPublished(a.published_at)}</span>
            {/if}
          </button>
        {:else}
          <p class="muted list-empty">
            {showingDrafts ? 'No drafted articles.' : 'No articles yet.'}
          </p>
        {/each}
        {#if !showingDrafts && nextCursor}
          <div class="scroll-sentinel" bind:this={sentinel}>
            {#if loadingMore}<span class="muted">Loading more…</span>{/if}
          </div>
        {/if}
      </aside>

      <div class="editor stack panel">
        {#if selectedId}
          <div class="editor-head">
            <span class="article-id mono">{selectedId}</span>
            {#if !loadingArticle && isDraft}
              <span class="draft-badge">Draft — admin only</span>
            {/if}
            {#if loadingArticle}
              <span class="loading-badge">Loading body…</span>
            {/if}
          </div>

          {#if !loadingArticle}
            <div class="share-links panel">
              <div class="share-links-head">
                <span>Share links</span>
                <input
                  type="text"
                  placeholder="Label (optional)"
                  bind:value={newShareLinkLabel}
                  maxlength="120"
                />
                <button
                  class="btn compact"
                  type="button"
                  disabled={creatingShareLink}
                  onclick={() => createShareLink()}
                >
                  {creatingShareLink ? 'Creating…' : 'Generate share link'}
                </button>
              </div>
              {#if shareLinks.length}
                <ul class="share-links-list">
                  {#each shareLinks as link (String(link.token))}
                    {@const token = String(link.token)}
                    {@const revoked = Boolean(link.revoked)}
                    <li class:revoked>
                      <code class="mono">/shared/{token}</code>
                      {#if link.label}<span class="admin-muted small">{String(link.label)}</span>{/if}
                      {#if revoked}
                        <span class="admin-muted small">revoked</span>
                      {:else}
                        <button class="btn compact" type="button" onclick={() => copyShareUrl(token)}>
                          {copiedToken === token ? 'Copied ✓' : 'Copy link'}
                        </button>
                        <button
                          class="btn compact btn-danger"
                          type="button"
                          onclick={() => revokeShareLink(token)}
                        >
                          Revoke
                        </button>
                      {/if}
                    </li>
                  {/each}
                </ul>
              {:else}
                <p class="admin-muted small">No share links yet for this article.</p>
              {/if}
            </div>
          {/if}

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
                      <AnnotatedMarkdown
                        source={body}
                        {comments}
                        allowCreate={false}
                        allowDelete={true}
                        onDeleteComment={deleteComment}
                      />
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
              disabled={loadingArticle || saving || deleting || recomposing}
              onclick={() => (deleteOpen = true)}
            >
              Delete
            </button>
            <button
              class="btn"
              type="button"
              disabled={loadingArticle || togglingDraft || saving || deleting}
              onclick={() => toggleDraft()}
            >
              {togglingDraft ? 'Working…' : isDraft ? 'Restore to live' : 'Move to draft'}
            </button>
            <button
              class="btn"
              type="button"
              disabled={loadingArticle}
              onclick={() => openHistory()}
            >
              History
            </button>
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              disabled={loadingArticle || recomposing || saving || deleting}
              onclick={() => recomposeSelected()}
            >
              {recomposing ? 'Triggering…' : 'Recompose'}
            </button>
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
              disabled={loadingArticle || saving || deleting || recomposing}
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

{#if historyOpen}
  <div class="overlay" role="dialog" aria-modal="true" aria-labelledby="history-title">
    <div class="dialog history-dialog panel stack">
      <div class="history-head">
        <h3 id="history-title">Version history{title.trim() ? ` — ${title.trim()}` : ''}</h3>
        <button class="btn" type="button" onclick={() => closeHistory()}>Close</button>
      </div>

      {#if loadingHistory}
        <p class="muted">Loading history…</p>
      {:else if versions.length === 0}
        <p class="muted">No prior versions — this article has never been edited or recomposed.</p>
      {:else}
        <div class="history-pickers">
          <label class="field">
            <span>Compare</span>
            <select
              value={diffFromVersion ?? ''}
              onchange={(e) => {
                diffFromVersion = Number((e.target as HTMLSelectElement).value)
                void runDiff()
              }}
            >
              {#each versions as v (v.version)}
                <option value={v.version}>
                  v{v.version} — {formatPublished(v.edited_at)} ({v.edit_reason})
                </option>
              {/each}
            </select>
          </label>
          <span class="arrow">→</span>
          <label class="field">
            <span>Against</span>
            <select
              value={diffToVersion}
              onchange={(e) => {
                diffToVersion = Number((e.target as HTMLSelectElement).value)
                void runDiff()
              }}
            >
              <option value={CURRENT_VERSION}>Current (live editor content)</option>
              {#each versions as v (v.version)}
                <option value={v.version}>
                  v{v.version} — {formatPublished(v.edited_at)} ({v.edit_reason})
                </option>
              {/each}
            </select>
          </label>
        </div>

        {#if diffError}<p class="err">{diffError}</p>{/if}

        {#if loadingDiff}
          <p class="muted">Computing diff…</p>
        {:else if diffOps}
          <div class="diff-stats">
            <span class="diff-added">+{diffStats.added}</span>
            <span class="diff-removed">−{diffStats.removed}</span>
          </div>
          <div class="diff-view">
            {#each diffOps as op, i (i)}
              <div class="diff-line diff-{op.kind}">{op.line || ' '}</div>
            {/each}
          </div>
        {/if}

        <div class="history-list">
          <p class="list-label">All versions</p>
          {#each versions as v (v.version)}
            <div class="history-item">
              <span class="history-version">v{v.version}</span>
              <span class="history-title">{v.title}</span>
              <span class="subtle">{formatPublished(v.edited_at)} · {v.editor} · {v.edit_reason}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .toolbar-actions {
    display: flex;
    gap: 8px;
  }
  .toolbar-actions .btn.on {
    background: var(--accent-soft);
    border-color: var(--primary);
  }
  .draft-badge {
    font-size: 0.78rem;
    font-weight: 700;
    color: var(--danger);
    border: 1px solid var(--danger);
    border-radius: 999px;
    padding: 2px 10px;
    flex-shrink: 0;
  }
  .share-links {
    padding: 10px 12px;
    margin: 10px 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .share-links-head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 0.85rem;
    font-weight: 600;
  }
  .share-links-head input {
    flex: 1;
    min-width: 140px;
    padding: 4px 8px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--callout);
    color: inherit;
    font: inherit;
    font-weight: 400;
  }
  .share-links-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .share-links-list li {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 0.85rem;
  }
  .share-links-list li.revoked {
    opacity: 0.6;
  }
  .share-links-list code {
    overflow-wrap: anywhere;
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
      grid-template-columns: 260px minmax(0, 1fr);
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
  .scroll-sentinel {
    display: flex;
    justify-content: center;
    padding: 10px 8px 4px;
    font-size: 0.82rem;
    min-height: 1px;
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
    /* Sized to the viewport rather than a fixed 360px: this is a writing
       surface on a wide console, and a laptop screen has far more room than
       the old fixed heights used. */
    min-height: 420px;
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
    min-height: min(64vh, 760px);
    resize: vertical;
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
    /* Matches the editor so the two panes read as one surface side by side. */
    max-height: min(64vh, 760px);
    min-height: min(64vh, 760px);
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
  .history-dialog {
    width: min(920px, 100%);
    max-height: min(85vh, 900px);
    overflow-y: auto;
  }
  .history-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .history-head h3 {
    font-size: 1.05rem;
  }
  .history-pickers {
    display: flex;
    align-items: flex-end;
    gap: 10px;
    flex-wrap: wrap;
  }
  .history-pickers .field {
    flex: 1;
    min-width: 220px;
  }
  .history-pickers .arrow {
    padding-bottom: 8px;
    color: var(--subtle);
  }
  .diff-stats {
    display: flex;
    gap: 12px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.85rem;
    font-weight: 700;
  }
  .diff-added {
    color: var(--good, #3a6b4a);
  }
  .diff-removed {
    color: var(--danger);
  }
  .diff-view {
    border: 1px solid var(--line, #ddd);
    border-radius: 8px;
    max-height: 420px;
    overflow: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .diff-line {
    padding: 1px 10px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .diff-equal {
    color: var(--muted);
  }
  .diff-add {
    background: color-mix(in srgb, var(--good, #3a6b4a) 16%, transparent);
    color: var(--on-surface);
  }
  .diff-remove {
    background: color-mix(in srgb, var(--danger) 14%, transparent);
    color: var(--on-surface);
    text-decoration: line-through;
    text-decoration-color: color-mix(in srgb, var(--danger) 60%, transparent);
  }
  .history-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-top: 8px;
    border-top: 1px solid var(--line, #ddd);
  }
  .history-item {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 8px;
    font-size: 0.85rem;
  }
  .history-version {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-weight: 700;
    color: var(--primary);
    min-width: 32px;
  }
  .history-title {
    font-weight: 600;
  }
</style>
