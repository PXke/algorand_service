<script lang="ts">
  /**
   * Two-pill switcher between the Marketplace page (/x402 and its
   * directory/board/requests/grades/register tabs -- ecosystem discovery
   * and vetting mechanics) and the Our Endpoints page (/x402/endpoints --
   * PXke's own direct-utility products). Deliberately NOT folded into
   * X402.svelte's flat tab bar: those tabs are all one page's internal
   * views, this switches the page itself.
   */
  import { messages, t } from '../../lib/i18n'
  import { navigate } from '../../lib/router'

  let { active }: { active: 'marketplace' | 'endpoints' } = $props()

  function go(href: string, e: MouseEvent) {
    e.preventDefault()
    navigate(href)
  }
</script>

<nav class="page-switch" aria-label={t($messages, 'x402Title')}>
  <a
    class="pill"
    class:active={active === 'marketplace'}
    href="/x402"
    onclick={(e) => go('/x402', e)}
  >
    {t($messages, 'x402NavMarketplace')}
  </a>
  <a
    class="pill"
    class:active={active === 'endpoints'}
    href="/x402/endpoints"
    onclick={(e) => go('/x402/endpoints', e)}
  >
    {t($messages, 'x402NavOurEndpoints')}
  </a>
</nav>

<style>
  .page-switch {
    display: flex;
    gap: 6px;
  }
  .pill {
    padding: 6px 14px;
    border: 1px solid var(--border);
    border-radius: 999px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    text-decoration: none;
    background: var(--surface);
  }
  .pill:hover {
    color: var(--accent);
    border-color: var(--accent);
    text-decoration: none;
  }
  .pill.active {
    color: var(--surface);
    background: var(--accent);
    border-color: var(--accent);
  }
</style>
