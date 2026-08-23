<script lang="ts">
  import { tick } from 'svelte'
  import Markdown from './Markdown.svelte'
  import {
    captureSelectionAnchor,
    clearMarks,
    resolveAnchorToRange,
    wrapRangeWithMark,
    type TextQuoteAnchor,
  } from '../lib/textHighlight'

  type CommentItem = {
    comment_id: string
    body: string
    author_name: string
    created_at_epoch: number
    anchor_quote?: string | null
    anchor_prefix?: string | null
    anchor_suffix?: string | null
  }

  let {
    source = '',
    comments = [],
    allowCreate = false,
    allowDelete = false,
    onCreateComment,
    onDeleteComment,
  }: {
    source?: string
    comments?: CommentItem[]
    allowCreate?: boolean
    allowDelete?: boolean
    onCreateComment?: (anchor: TextQuoteAnchor | null, body: string) => Promise<CommentItem | void>
    onDeleteComment?: (commentId: string) => Promise<void>
  } = $props()

  let wrapperEl: HTMLDivElement | undefined = $state()
  let orphanedIds = $state<Set<string>>(new Set())
  let activeCommentId = $state<string | null>(null)
  let popoverPos = $state<{ top: number; left: number } | null>(null)

  let pendingAnchor = $state<TextQuoteAnchor | null>(null)
  let composerPos = $state<{ top: number; left: number } | null>(null)
  let composerBody = $state('')
  let composerError = $state<string | null>(null)
  let submitting = $state(false)

  function commentsById(): Map<string, CommentItem> {
    return new Map(comments.map((c) => [c.comment_id, c]))
  }

  const orphanedComments = $derived(comments.filter((c) => orphanedIds.has(c.comment_id)))

  $effect(() => {
    // Re-run whenever the rendered body or the comment list changes.
    void source
    void comments
    tick().then(placeHighlights)
  })

  function focusComposer(node: HTMLTextAreaElement) {
    $effect(() => {
      if (composerPos) node.focus()
    })
  }

  function placeHighlights(): void {
    const container = wrapperEl?.querySelector<HTMLElement>('.md')
    if (!container) return
    clearMarks(container)
    const nextOrphaned = new Set<string>()
    for (const comment of comments) {
      if (!comment.anchor_quote) continue
      const anchor: TextQuoteAnchor = {
        quote: comment.anchor_quote,
        prefix: comment.anchor_prefix ?? '',
        suffix: comment.anchor_suffix ?? '',
      }
      const range = resolveAnchorToRange(container, anchor)
      if (!range) {
        nextOrphaned.add(comment.comment_id)
        continue
      }
      wrapRangeWithMark(range, { 'data-comment-id': comment.comment_id })
    }
    orphanedIds = nextOrphaned
  }

  function inComposer(target: EventTarget | null): boolean {
    return target instanceof Element && Boolean(target.closest('.comment-composer'))
  }

  function showCommentAt(mark: Element): void {
    const id = mark.getAttribute('data-comment-id')
    if (!id || !wrapperEl) return
    const rect = mark.getBoundingClientRect()
    const wrapperRect = wrapperEl.getBoundingClientRect()
    activeCommentId = id
    popoverPos = {
      top: rect.bottom - wrapperRect.top + 6,
      left: rect.left - wrapperRect.left,
    }
  }

  function onContainerClick(event: MouseEvent): void {
    if (inComposer(event.target)) return
    const target = event.target as HTMLElement
    const mark = target.closest('mark[data-comment-id]')
    if (!mark) {
      activeCommentId = null
      popoverPos = null
      return
    }
    showCommentAt(mark)
  }

  function onSelectionChange(event: MouseEvent): void {
    if (!allowCreate || !wrapperEl) return
    // Clicking the composer collapses the article selection. That used to
    // look like "click away" and unmount the box before the click landed —
    // so the textarea couldn't be focused, and Comment never submitted.
    if (inComposer(event.target)) return

    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      if (composerPos) cancelComposer()
      return
    }
    const range = selection.getRangeAt(0)
    const container = wrapperEl.querySelector<HTMLElement>('.md')
    if (!container || !container.contains(range.commonAncestorContainer)) {
      if (composerPos) cancelComposer()
      return
    }
    const anchor = captureSelectionAnchor(container, range)
    if (!anchor) {
      if (composerPos) cancelComposer()
      return
    }
    const rect = range.getBoundingClientRect()
    const wrapperRect = wrapperEl.getBoundingClientRect()
    pendingAnchor = anchor
    composerBody = composerPos ? composerBody : ''
    composerError = null
    composerPos = { top: rect.bottom - wrapperRect.top + 8, left: rect.left - wrapperRect.left }
  }

  function onComposerMouseDown(event: MouseEvent): void {
    // Keep the highlight visible when clicking Comment/Cancel.
    event.preventDefault()
  }

  function onComposerKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.preventDefault()
      cancelComposer()
    }
  }

  async function submitComment(): Promise<void> {
    if (!onCreateComment || !composerBody.trim() || !pendingAnchor) return
    submitting = true
    composerError = null
    const anchor = $state.snapshot(pendingAnchor)
    try {
      // Author name is threaded by the caller's onCreateComment closure
      // (SharedArticle.svelte owns the reviewer-name prompt/persistence).
      const created = await onCreateComment(anchor, composerBody.trim())
      const newId = created?.comment_id
      composerBody = ''
      composerPos = null
      pendingAnchor = null
      window.getSelection()?.removeAllRanges()
      await tick()
      placeHighlights()
      await tick()
      const mark = newId
        ? wrapperEl?.querySelector(`mark[data-comment-id="${CSS.escape(newId)}"]`)
        : null
      if (mark) showCommentAt(mark)
    } catch (e) {
      composerError = e instanceof Error ? e.message : String(e)
    } finally {
      submitting = false
    }
  }

  function cancelComposer(): void {
    composerPos = null
    pendingAnchor = null
    composerBody = ''
    composerError = null
  }

  async function deleteActive(): Promise<void> {
    if (!activeCommentId || !onDeleteComment) return
    await onDeleteComment(activeCommentId)
    activeCommentId = null
    popoverPos = null
  }

  const activeComment = $derived(activeCommentId ? (commentsById().get(activeCommentId) ?? null) : null)
</script>

<div
  class="annotated-md"
  bind:this={wrapperEl}
  onclick={onContainerClick}
  onmouseup={onSelectionChange}
  role="presentation"
>
  <Markdown {source} />

  {#if activeComment && popoverPos}
    <div class="comment-popover" style="top: {popoverPos.top}px; left: {popoverPos.left}px">
      <div class="comment-meta">
        {activeComment.author_name || 'Reviewer'} ·
        {new Date(activeComment.created_at_epoch * 1000).toLocaleDateString()}
      </div>
      <p class="comment-body">{activeComment.body}</p>
      {#if allowDelete}
        <button type="button" class="btn compact btn-danger" onclick={deleteActive}>
          Delete
        </button>
      {/if}
    </div>
  {/if}

  {#if allowCreate && composerPos}
    <div
      class="comment-composer"
      style="top: {composerPos.top}px; left: {composerPos.left}px"
    >
      <textarea
        {@attach focusComposer}
        bind:value={composerBody}
        placeholder="Add a comment on this passage…"
        aria-label="Comment on this passage"
        rows="3"
        onkeydown={onComposerKeyDown}
      ></textarea>
      {#if composerError}
        <p class="composer-error">{composerError}</p>
      {/if}
      <div class="composer-actions">
        <button type="button" class="btn compact" onmousedown={onComposerMouseDown} onclick={cancelComposer}>Cancel</button>
        <button
          type="button"
          class="btn compact primary"
          disabled={submitting || !composerBody.trim() || !pendingAnchor}
          onmousedown={onComposerMouseDown}
          onclick={submitComment}
        >
          {submitting ? 'Posting…' : 'Comment'}
        </button>
      </div>
    </div>
  {/if}
</div>

{#if orphanedComments.length}
  <div class="orphaned-comments">
    <h4>Comments not found in the current text</h4>
    <p class="admin-muted small">
      These were anchored to text that no longer appears in the article (it was likely edited).
    </p>
    {#each orphanedComments as comment (comment.comment_id)}
      <div class="orphaned-comment">
        {#if comment.anchor_quote}
          <blockquote>{comment.anchor_quote}</blockquote>
        {/if}
        <div class="comment-meta">
          {comment.author_name || 'Reviewer'} ·
          {new Date(comment.created_at_epoch * 1000).toLocaleDateString()}
        </div>
        <p class="comment-body">{comment.body}</p>
        {#if allowDelete && onDeleteComment}
          <button
            type="button"
            class="btn compact btn-danger"
            onclick={() => onDeleteComment?.(comment.comment_id)}
          >
            Delete
          </button>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .annotated-md {
    position: relative;
  }

  .annotated-md :global(mark[data-comment-id]) {
    background: color-mix(in srgb, var(--accent) 28%, transparent);
    color: inherit;
    border-radius: 2px;
    cursor: pointer;
    padding: 0 1px;
  }

  .comment-popover,
  .comment-composer {
    position: absolute;
    z-index: 20;
    width: min(320px, 80vw);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
    padding: 12px 14px;
  }

  .comment-meta {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: 6px;
  }

  .comment-body {
    margin: 0 0 10px;
    font-size: 0.92rem;
    line-height: 1.4;
    white-space: pre-wrap;
  }

  .comment-composer textarea {
    width: 100%;
    resize: vertical;
    font: inherit;
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--callout);
    color: inherit;
  }

  .composer-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 8px;
  }

  .composer-error {
    color: var(--danger, #c62828);
    font-size: 0.85rem;
    margin: 6px 0 0;
  }

  .orphaned-comments {
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px dashed var(--border);
  }

  .orphaned-comments h4 {
    margin: 0 0 4px;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--muted);
  }

  .orphaned-comment {
    margin-top: 14px;
    padding: 10px 0;
    border-top: 1px solid var(--border);
  }

  .orphaned-comment blockquote {
    margin: 0 0 8px;
    padding-left: 12px;
    border-left: 3px solid var(--border);
    color: var(--muted);
    font-style: italic;
  }
</style>
