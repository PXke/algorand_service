<script lang="ts">
  import {
    ecosystemApi,
    ecosystemCategoryLabel,
    ECOSYSTEM_CATEGORIES,
    ECOSYSTEM_CATEGORY_SUGGESTION_MAX_LENGTH,
    ECOSYSTEM_DESCRIPTION_MAX_LENGTH,
    ECOSYSTEM_DESCRIPTION_MIN_LENGTH,
    ECOSYSTEM_MAX_TAGS,
    ECOSYSTEM_NAME_MAX_LENGTH,
  } from '../lib/api/ecosystem'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { ApiException } from '../lib/api/client'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'

  let name = $state('')
  let url = $state('')
  let description = $state('')
  let category = $state('other')
  let categorySuggestion = $state('')
  let tagsText = $state('')
  let contact = $state('')
  // Honeypot: real users never see or fill this (offscreen, not display:none
  // — some bots skip display:none fields but still fill offscreen ones,
  // same contact-form mechanism this mirrors).
  let website = $state('')

  let submitting = $state(false)
  let error = $state<string | null>(null)
  let result: { submissionId: string; statusUrl?: string } | null = $state(null)

  const descriptionLeft = $derived(ECOSYSTEM_DESCRIPTION_MAX_LENGTH - description.length)

  function parseTags(): string[] {
    return tagsText
      .split(',')
      .map((tg) => tg.trim().toLowerCase())
      .filter(Boolean)
      .slice(0, ECOSYSTEM_MAX_TAGS)
  }

  async function submit(e: Event) {
    e.preventDefault()
    error = null
    if (description.trim().length < ECOSYSTEM_DESCRIPTION_MIN_LENGTH) {
      error = `The description needs at least ${ECOSYSTEM_DESCRIPTION_MIN_LENGTH} characters — one plain sentence about what the project does.`
      return
    }
    submitting = true
    try {
      const res = await ecosystemApi.submit({
        name: name.trim(),
        url: url.trim(),
        description: description.trim(),
        category,
        category_suggestion: categorySuggestion.trim(),
        tags: parseTags(),
        contact: contact.trim(),
        website,
      })
      result = { submissionId: res.submission_id, statusUrl: res.status_url }
    } catch (err) {
      error = err instanceof ApiException ? err.userMessage : t($messages, 'errorGeneric')
    } finally {
      submitting = false
    }
  }
</script>

<PageMeta title="Submit a project" description={SITE_TAGLINE} path="/registry/submit" />

<div class="page submit">
  <nav class="crumbs" aria-label="Breadcrumb">
    <a
      href="/registry"
      onclick={(e) => {
        e.preventDefault()
        navigate('/registry')
      }}
    >
      Registry
    </a>
  </nav>

  <header class="head">
    <h1>Submit a project</h1>
    <p class="lead">
      Free, no wallet needed. A person reviews every submission, usually within two days. Keep the
      link this page gives you: it is the only way to check on your submission.
    </p>
  </header>

  {#if result}
    <div class="sent">
      <h2>Submitted</h2>
      <p>Your project is in the review queue.</p>
      {#if result.statusUrl}
        <p>Check on it any time at <code>{result.statusUrl}</code></p>
      {/if}
      <a
        class="btn btn-outlined"
        href="/registry"
        onclick={(e) => {
          e.preventDefault()
          navigate('/registry')
        }}
      >
        Back to the registry
      </a>
    </div>
  {:else}
    <form class="fields" onsubmit={submit}>
      <label class="field">
        <span class="label">Project name</span>
        <input bind:value={name} required maxlength={ECOSYSTEM_NAME_MAX_LENGTH} />
      </label>

      <label class="field">
        <span class="label">Website</span>
        <input type="url" bind:value={url} required placeholder="https://" />
        <span class="hint">No site yet? A GitHub repository works.</span>
      </label>

      <label class="field">
        <span class="label">What it does</span>
        <textarea
          rows="6"
          maxlength={ECOSYSTEM_DESCRIPTION_MAX_LENGTH}
          bind:value={description}
          required
          placeholder="What it does, in plain language. Skip the superlatives."
        ></textarea>
        <span class="hint">
          {ECOSYSTEM_DESCRIPTION_MIN_LENGTH} to {ECOSYSTEM_DESCRIPTION_MAX_LENGTH} characters.
          Markdown is fine — multiple paragraphs, links, lists.{description.length
            ? ` ${descriptionLeft} left.`
            : ''}
        </span>
      </label>

      <label class="field">
        <span class="label">Category</span>
        <select bind:value={category}>
          {#each ECOSYSTEM_CATEGORIES as c (c)}
            <option value={c}>{ecosystemCategoryLabel(c)}</option>
          {/each}
        </select>
      </label>

      {#if category === 'other'}
        <label class="field">
          <span class="label">Suggest a category name</span>
          <input
            bind:value={categorySuggestion}
            maxlength={ECOSYSTEM_CATEGORY_SUGGESTION_MAX_LENGTH}
            placeholder="e.g. Prediction markets"
          />
          <span class="hint">
            Optional. None of the existing categories fit? Tell us what to call it — a reviewer
            decides whether it becomes a real category.
          </span>
        </label>
      {/if}

      <label class="field">
        <span class="label">Tags</span>
        <input bind:value={tagsText} placeholder="wallet, multisig" />
        <span class="hint">Up to {ECOSYSTEM_MAX_TAGS}, separated by commas. Optional.</span>
      </label>

      <label class="field">
        <span class="label">Contact</span>
        <input bind:value={contact} placeholder="you@example.com or a wallet address" />
        <span class="hint">Optional and never shown. Used only if the reviewer has a question.</span>
      </label>

      <label class="honeypot" aria-hidden="true">
        <span>Leave this field empty</span>
        <input bind:value={website} tabindex="-1" autocomplete="off" />
      </label>

      <div class="actions">
        <button class="btn btn-outlined send" type="submit" disabled={submitting}>
          {#if submitting}
            <span class="spinner" aria-hidden="true"></span>
          {/if}
          Submit for review
        </button>
      </div>
    </form>
  {/if}

  {#if error}
    <p class="err" role="alert">{error}</p>
  {/if}
</div>

<style>
  .submit {
    display: flex;
    flex-direction: column;
    gap: 24px;
    max-width: 640px;
  }

  .crumbs {
    font-size: 13px;
  }
  .crumbs a {
    color: var(--muted);
    text-decoration: none;
  }
  .crumbs a:hover {
    color: var(--on-surface);
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  .head {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .head h1 {
    margin: 0;
    font-size: clamp(24px, 3vw, 30px);
    line-height: 1.15;
    letter-spacing: -0.3px;
  }
  .lead {
    margin: 0;
    color: var(--muted);
    font-size: 15px;
    line-height: 1.5;
  }

  .fields {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .label {
    font-size: 14px;
    font-weight: 600;
  }
  .hint {
    color: var(--subtle);
    font-size: 13px;
  }
  .field input,
  .field textarea,
  .field select {
    width: 100%;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    color: var(--on-surface);
    font-family: var(--font-sans);
    font-size: 15px;
    padding: 10px 12px;
  }
  .field textarea {
    font-family: var(--font-serif);
    font-size: 16px;
    line-height: 1.55;
    resize: vertical;
  }
  .field input:focus,
  .field textarea:focus,
  .field select:focus {
    border-color: var(--accent);
    outline: none;
  }

  /* Honeypot: offscreen, not display:none/visibility:hidden — some simple
     bots skip those but still autofill an offscreen field. */
  .honeypot {
    position: absolute;
    left: -9999px;
    width: 1px;
    height: 1px;
    overflow: hidden;
  }

  .actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px 20px;
    margin-top: 4px;
  }
  .send {
    min-height: 44px;
    padding-inline: 18px;
  }
  .send:disabled {
    opacity: 0.65;
    cursor: not-allowed;
  }
  .spinner {
    width: 16px;
    height: 16px;
    border: 2px solid color-mix(in srgb, var(--on-surface) 25%, transparent);
    border-top-color: var(--on-surface);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    margin-inline-end: 6px;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation: none;
    }
  }

  .sent {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    padding: 20px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--panel);
  }
  .sent h2 {
    margin: 0;
    font-size: 18px;
  }
  .sent p {
    margin: 0;
  }
  .sent code {
    font-size: 13px;
    overflow-wrap: anywhere;
  }

  .err {
    margin: 0;
    color: var(--danger);
  }
</style>
