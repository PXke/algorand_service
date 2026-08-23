<script lang="ts">
  import { messages, t } from '../lib/i18n'
  import { api, ApiException } from '../lib/api/client'
  import Icon from '../components/Icon.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'

  let name = $state('')
  let email = $state('')
  let message = $state('')
  let sent = $state(false)
  let error = $state<string | null>(null)
  let busy = $state(false)

  async function submit(e: Event) {
    e.preventDefault()
    if (message.trim().length < 10) {
      error = t($messages, 'contactTooShort')
      return
    }
    busy = true
    error = null
    try {
      await api.postJson('/api/v1/contact', {
        name: name.trim(),
        email: email.trim(),
        message: message.trim(),
      })
      sent = true
    } catch (err) {
      error = err instanceof ApiException ? err.userMessage : t($messages, 'errorGeneric')
    } finally {
      busy = false
    }
  }
</script>

<PageMeta
  title={t($messages, 'contactTitle')}
  description={t($messages, 'contactSubtitle') || SITE_TAGLINE}
  path="/contact"
/>

<div class="page stack contact">
  <header>
    <span class="accent-slug"></span>
    <h1>{t($messages, 'contactTitle')}</h1>
    <p class="lead muted">{t($messages, 'contactSubtitle')}</p>
  </header>

  <div class="form-wrap">
    {#if sent}
      <div class="sent">
        <p class="kicker">Dispatch</p>
        <p>{t($messages, 'contactSent')}</p>
      </div>
    {:else}
      <form class="fields" onsubmit={submit}>
        <label class="field">
          <span>{t($messages, 'contactNameLabel')}</span>
          <input bind:value={name} autocomplete="name" />
        </label>
        <label class="field">
          <span>{t($messages, 'contactEmailLabel')}</span>
          <input type="email" bind:value={email} autocomplete="email" />
        </label>
        <label class="field">
          <span>{t($messages, 'contactMessageLabel')}</span>
          <textarea
            rows="8"
            maxlength="4000"
            bind:value={message}
            placeholder={t($messages, 'contactMessageHint')}
            required
          ></textarea>
        </label>
        <div class="actions">
          <button class="btn btn-outlined send" type="submit" disabled={busy}>
            {#if busy}
              <span class="spinner" aria-hidden="true"></span>
            {:else}
              <Icon name="send" size={18} />
            {/if}
            {t($messages, 'contactSend')}
          </button>
        </div>
      </form>
    {/if}
  </div>

  {#if error}
    <p class="err banner">{error}</p>
  {/if}
</div>

<style>
  .contact {
    gap: 16px;
  }
  h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 40rem;
  }
  .form-wrap {
    max-width: var(--max-reading);
  }
  .fields {
    display: flex;
    flex-direction: column;
    gap: 12px;
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
  .field textarea {
    background: var(--surface);
    font-family: var(--font-mono);
    font-size: 14px;
  }
  .field textarea {
    font-family: var(--font-serif);
    font-size: 16px;
    line-height: 1.55;
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
    padding: 8px 0 24px;
  }
  .sent p:not(.kicker) {
    margin: 0;
    max-width: 36rem;
    font-family: var(--font-serif);
    font-size: 1.12rem;
    line-height: 1.55;
    color: var(--on-surface);
  }
  .banner {
    margin: 0;
    padding: 12px 14px;
    border-radius: 12px;
    background: color-mix(in srgb, var(--danger) 12%, var(--panel));
    border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--border));
  }
  .err {
    color: var(--danger);
  }
</style>
