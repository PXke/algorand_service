<script lang="ts">
  import { sharingApi, type CommentItem } from '../lib/api/sharing'
  import { ApiException } from '../lib/api/client'
  import AnnotatedMarkdown from '../components/AnnotatedMarkdown.svelte'
  import type { TextQuoteAnchor } from '../lib/textHighlight'

  let { token }: { token: string } = $props()

  const AUTHOR_NAME_KEY = 'sharing:reviewer_name'

  let loading = $state(true)
  let error = $state<string | null>(null)
  let revoked = $state(false)
  let article = $state<Record<string, unknown> | null>(null)
  let isDraft = $state(false)
  let linkLabel = $state('')
  let comments = $state<CommentItem[]>([])
  let authorName = $state('')

  $effect(() => {
    authorName = localStorage.getItem(AUTHOR_NAME_KEY) || ''
  })

  $effect(() => {
    const t = token
    loading = true
    error = null
    revoked = false
    void (async () => {
      try {
        const [articleResp, commentsResp] = await Promise.all([
          sharingApi.fetchSharedArticle(t),
          sharingApi.listSharedComments(t),
        ])
        article = articleResp.article
        isDraft = articleResp.is_draft
        linkLabel = articleResp.link_label
        comments = commentsResp.items
      } catch (e) {
        if (e instanceof ApiException && e.statusCode === 403) {
          revoked = true
        } else {
          error = e instanceof Error ? e.message : String(e)
        }
      } finally {
        loading = false
      }
    })()
  })

  function saveAuthorName(): void {
    localStorage.setItem(AUTHOR_NAME_KEY, authorName.trim())
  }

  async function createComment(anchor: TextQuoteAnchor | null, body: string) {
    saveAuthorName()
    const created = await sharingApi.postSharedComment(token, body, authorName.trim(), anchor)
    comments = [...comments, created]
    return created
  }
</script>

<div class="shared-page">
  {#if loading}
    <p class="admin-muted">Loading…</p>
  {:else if revoked}
    <div class="shared-notice">
      <h1>This link has been revoked</h1>
      <p>Ask the person who shared it with you for a new link.</p>
    </div>
  {:else if error}
    <div class="shared-notice">
      <h1>Couldn't load this article</h1>
      <p class="admin-muted">{error}</p>
    </div>
  {:else if article}
    <header class="shared-header">
      {#if isDraft}
        <span class="draft-badge">Draft — shared preview{linkLabel ? ` · ${linkLabel}` : ''}</span>
      {:else}
        <span class="draft-badge live">Now published{linkLabel ? ` · ${linkLabel}` : ''}</span>
      {/if}
      <h1>{String(article.title ?? '')}</h1>
    </header>

    <div class="reviewer-name">
      <label for="reviewer-name-input">Your name (shown on your comments)</label>
      <input
        id="reviewer-name-input"
        type="text"
        bind:value={authorName}
        onblur={saveAuthorName}
        placeholder="Reviewer"
        maxlength="80"
      />
    </div>

    <p class="admin-muted small">
      Highlight any passage below to leave a comment on it.
    </p>

    <AnnotatedMarkdown
      source={String(article.body ?? '')}
      {comments}
      allowCreate={true}
      allowDelete={false}
      onCreateComment={createComment}
    />
  {/if}
</div>

<style>
  .shared-page {
    max-width: 720px;
    margin: 0 auto;
    padding: 32px 20px 80px;
  }

  .shared-notice {
    text-align: center;
    padding: 60px 20px;
  }

  .shared-header {
    margin-bottom: 20px;
  }

  .draft-badge {
    display: inline-block;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.9px;
    text-transform: uppercase;
    padding: 0;
    color: var(--accent);
    margin-bottom: 10px;
  }

  .draft-badge.live {
    color: var(--gain);
  }

  .shared-header h1 {
    font-family: var(--font-display);
    font-size: 30px;
    margin: 0;
  }

  .reviewer-name {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 16px 0;
    font-size: 0.85rem;
  }

  .reviewer-name input {
    flex: 1;
    max-width: 240px;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--callout);
    color: inherit;
    font: inherit;
  }
</style>
