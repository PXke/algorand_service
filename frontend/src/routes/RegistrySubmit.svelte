<script lang="ts">
  import {
    ecosystemApi,
    ECOSYSTEM_CATEGORIES,
    ECOSYSTEM_DESCRIPTION_MAX_LENGTH,
    ECOSYSTEM_DESCRIPTION_MIN_LENGTH,
    ECOSYSTEM_MAX_TAGS,
    ECOSYSTEM_NAME_MAX_LENGTH,
  } from '../lib/api/ecosystem'
  import { messages, t } from '../lib/i18n'
  import { ApiException } from '../lib/api/client'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'

  let name = $state('')
  let url = $state('')
  let description = $state('')
  let category = $state('other')
  let tagsText = $state('')
  let contact = $state('')
  // Honeypot: real users never see or fill this (offscreen, not display:none
  // — some bots skip display:none fields but still fill offscreen ones,
  // same contact-form mechanism this mirrors).
  let website = $state('')

  let submitting = $state(false)
  let error = $state<string | null>(null)
  let result: { submissionId: string; statusUrl?: string } | null = $state(null)

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
      error = `Description must be at least ${ECOSYSTEM_DESCRIPTION_MIN_LENGTH} characters — one factual sentence.`
      return
    }
    submitting = true
    try {
      const res = await ecosystemApi.submit({
        name: name.trim(),
        url: url.trim(),
        description: description.trim(),
        category,
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

<div class="page stack submit-page">
  <header class="compact-head">
    <p class="kicker">Registry</p>
    <h1>Submit a project</h1>
    <p class="lead muted">
      Free, no wallet needed. A human reviews every submission, usually within 48 hours — this
      page's own status link is your only notification, so bookmark it.
    </p>
  </header>

  {#if result}
    <div class="sent panel">
      <p class="kicker">Submitted</p>
      <p>Thanks — your submission is pending review.</p>
      {#if result.statusUrl}
        <p class="muted">
          Check status any time at
          <code>{result.statusUrl}</code>
        </p>
      {/if}
    </div>
  {:else}
    <form class="fields" onsubmit={submit}>
      <label class="field">
        <span>Name</span>
        <input bind:value={name} required maxlength={ECOSYSTEM_NAME_MAX_LENGTH} placeholder="Project name" />
      </label>
      <label class="field">
        <span>URL</span>
        <input type="url" bind:value={url} required placeholder="https://example.com or your GitHub repo" />
        <p class="hint muted">Your project's site — no separate site yet? Your GitHub repo works fine.</p>
      </label>
      <label class="field">
        <span>Description ({ECOSYSTEM_DESCRIPTION_MIN_LENGTH}-{ECOSYSTEM_DESCRIPTION_MAX_LENGTH} characters, one factual sentence)</span>
        <textarea
          rows="3"
          maxlength={ECOSYSTEM_DESCRIPTION_MAX_LENGTH}
          bind:value={description}
          required
          placeholder="What does it do, plainly — no 'revolutionary' or 'the first'."
        ></textarea>
      </label>
      <label class="field">
        <span>Category</span>
        <select bind:value={category}>
          {#each ECOSYSTEM_CATEGORIES as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
      </label>
      <label class="field">
        <span>Tags (comma-separated, up to {ECOSYSTEM_MAX_TAGS})</span>
        <input bind:value={tagsText} placeholder="wallet, multisig" />
      </label>
      <p class="x402-note muted">
        Sell a paid x402 endpoint? That belongs in
        <a href="https://x402.pxke.me/list" target="_blank" rel="noopener noreferrer">the x402 marketplace</a>,
        not here — this registry is the free, general Algorand ecosystem directory.
      </p>
      <label class="field">
        <span>Contact (optional, private — email or wallet, never shown publicly)</span>
        <input bind:value={contact} placeholder="you@example.com" />
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
    <p class="err banner">{error}</p>
  {/if}
</div>

<style>
  .submit-page {
    gap: 16px;
  }
  .fields {
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: var(--max-reading);
  }
  .hint {
    margin: 4px 0 0;
    font-size: 12px;
  }
  .x402-note {
    margin: 0;
    font-size: 12px;
    max-width: var(--max-reading);
  }
  .x402-note a {
    color: var(--accent);
  }
  .field span {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .field input,
  .field textarea,
  .field select {
    background: var(--surface);
    font-family: var(--font-mono);
    font-size: 14px;
  }
  .field textarea {
    font-family: var(--font-serif);
    font-size: 16px;
    line-height: 1.55;
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
    justify-content: flex-end;
    margin-top: 4px;
  }
  .send {
    min-height: 48px;
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
  .sent {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    padding: 16px 20px;
    max-width: var(--max-reading);
  }
  .sent code {
    font-size: 12px;
  }
  .err {
    color: var(--danger);
  }
</style>
