<script lang="ts">
  import {
    ecosystemApi,
    ECOSYSTEM_REQUEST_MESSAGE_MAX_LENGTH,
    ECOSYSTEM_REQUEST_MESSAGE_MIN_LENGTH,
    type EcosystemRequestKind,
  } from '../lib/api/ecosystem'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { ApiException } from '../lib/api/client'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'

  let { slug }: { slug: string } = $props()

  let kind = $state<EcosystemRequestKind>('change')
  let message = $state('')
  let contact = $state('')
  // Honeypot: real users never see or fill this (offscreen, not display:none
  // — same contact-form mechanism RegistrySubmit.svelte mirrors).
  let website = $state('')

  let submitting = $state(false)
  let error = $state<string | null>(null)
  let sent = $state(false)

  const messageLeft = $derived(ECOSYSTEM_REQUEST_MESSAGE_MAX_LENGTH - message.length)

  async function submit(e: Event) {
    e.preventDefault()
    error = null
    if (message.trim().length < ECOSYSTEM_REQUEST_MESSAGE_MIN_LENGTH) {
      error = `Say a bit more — at least ${ECOSYSTEM_REQUEST_MESSAGE_MIN_LENGTH} characters about what should change.`
      return
    }
    submitting = true
    try {
      await ecosystemApi.submitRequest(slug, {
        kind,
        message: message.trim(),
        contact: contact.trim(),
        website,
      })
      sent = true
    } catch (err) {
      error = err instanceof ApiException ? err.userMessage : t($messages, 'errorGeneric')
    } finally {
      submitting = false
    }
  }
</script>

<PageMeta title="Suggest a change" description={SITE_TAGLINE} path={`/registry/${slug}/request`} />

<div class="page submit">
  <nav class="crumbs" aria-label="Breadcrumb">
    <a
      href={`/registry/${slug}`}
      onclick={(e) => {
        e.preventDefault()
        navigate(`/registry/${slug}`)
      }}
    >
      Back to entry
    </a>
  </nav>

  <header class="head">
    <h1>Suggest a change</h1>
    <p class="lead">
      Free, no wallet needed. Tell us what's wrong or out of date — a person reviews every request,
      usually within a couple of days.
    </p>
  </header>

  {#if sent}
    <div class="sent">
      <h2>Sent</h2>
      <p>Thanks — a reviewer will take a look.</p>
      <a
        class="btn btn-outlined"
        href={`/registry/${slug}`}
        onclick={(e) => {
          e.preventDefault()
          navigate(`/registry/${slug}`)
        }}
      >
        Back to entry
      </a>
    </div>
  {:else}
    <form class="fields" onsubmit={submit}>
      <fieldset class="field kind">
        <legend class="label">What kind of change?</legend>
        <label class="radio">
          <input type="radio" name="kind" value="change" bind:group={kind} />
          <span>Something's wrong or out of date</span>
        </label>
        <label class="radio">
          <input type="radio" name="kind" value="removal" bind:group={kind} />
          <span>This shouldn't be listed</span>
        </label>
      </fieldset>

      <label class="field">
        <span class="label">What should change?</span>
        <textarea
          rows="6"
          maxlength={ECOSYSTEM_REQUEST_MESSAGE_MAX_LENGTH}
          bind:value={message}
          required
          placeholder={kind === 'removal'
            ? 'Why should this be removed?'
            : "What's out of date or incorrect, and what should it say instead?"}
        ></textarea>
        <span class="hint">
          {ECOSYSTEM_REQUEST_MESSAGE_MIN_LENGTH} to {ECOSYSTEM_REQUEST_MESSAGE_MAX_LENGTH} characters.{message.length
            ? ` ${messageLeft} left.`
            : ''}
        </span>
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
          Send
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
    border: 0;
    padding: 0;
    margin: 0;
  }
  .label {
    font-size: 14px;
    font-weight: 600;
    padding: 0;
  }
  .hint {
    color: var(--subtle);
    font-size: 13px;
  }
  .field input,
  .field textarea {
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
  .field textarea:focus {
    border-color: var(--accent);
    outline: none;
  }

  .kind {
    gap: 8px;
  }
  .radio {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 15px;
  }
  .radio input {
    width: auto;
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

  .err {
    margin: 0;
    color: var(--danger);
  }
</style>
