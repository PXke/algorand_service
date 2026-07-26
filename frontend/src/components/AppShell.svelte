<script lang="ts">
  import { messages, t, localePreference, localeOptions } from '../lib/i18n'
  import { themeMode, resolvedTheme, toggleLightDark } from '../lib/theme'
  import { walletAddress, isAdmin, logout } from '../lib/auth/session'
  import { setAnalyticsOptOut } from '../lib/analyticsOptOut'
  import { config } from '../lib/config'
  import { navigate, pathOnly } from '../lib/router'
  import BrandMark from './BrandMark.svelte'
  import Icon from './Icon.svelte'
  import MarketsBar from './MarketsBar.svelte'
  import SiteFooter from './SiteFooter.svelte'
  import type { Component } from 'svelte'

  let { children }: { children: import('svelte').Snippet } = $props()

  let drawerOpen = $state(false)
  let walletOpen = $state(false)
  let WalletDialog = $state<Component<{ onclose: () => void }> | null>(null)
  let appsOpen = $state(false)
  let localeOpen = $state(false)
  let appsWrapEl = $state<HTMLElement | null>(null)
  let localeWrapEl = $state<HTMLElement | null>(null)

  // Admin wallet → pxke_no_track cookie so our visits aren't counted.
  $effect(() => {
    setAnalyticsOptOut(!!$isAdmin)
  })

  $effect(() => {
    if (!walletOpen || WalletDialog) return
    void import('./WalletDialog.svelte').then((m) => {
      WalletDialog = m.default
    })
  })

  const nav = $derived([
    {
      href: '/news',
      label: t($messages, 'navLatest'),
      icon: 'bolt' as const,
      match: (p: string) => p === '/news',
    },
    {
      href: '/hot',
      label: t($messages, 'navHot'),
      icon: 'fire' as const,
      match: (p: string) => p === '/hot',
    },
    {
      href: '/top',
      label: t($messages, 'navTop'),
      icon: 'trending' as const,
      match: (p: string) => p === '/top',
    },
    {
      href: '/topics',
      label: t($messages, 'navTopics'),
      icon: 'tag' as const,
      match: (p: string) => p === '/topics' || p.startsWith('/topic/'),
    },
    {
      href: '/search',
      label: t($messages, 'navSearch'),
      icon: 'search' as const,
      match: (p: string) => p === '/search' || p.startsWith('/search/'),
    },
    {
      href: '/about',
      label: t($messages, 'navAbout'),
      icon: 'info' as const,
      match: (p: string) => p === '/about',
    },
    {
      href: '/contact',
      label: t($messages, 'navContact'),
      icon: 'mail' as const,
      match: (p: string) => p === '/contact',
    },
  ])

  const products = $derived(
    [
      {
        href: '/',
        label: t($messages, 'navNews'),
        tagline: t($messages, 'homeNewsDescription'),
        icon: 'menu_book' as const,
        active: (p: string) =>
          p === '/' ||
          p.startsWith('/news') ||
          p === '/hot' ||
          p === '/top' ||
          p === '/topics' ||
          p.startsWith('/topic/') ||
          p === '/about' ||
          p === '/contact',
      },
      {
        href: '/search',
        label: t($messages, 'navSearch'),
        tagline: t($messages, 'homeSearchDescription'),
        icon: 'search' as const,
        active: (p: string) => p === '/search' || p.startsWith('/search/'),
      },
      ...(config.suggestionsEnabled
        ? [
            {
              href: '/suggestions',
              label: t($messages, 'navSuggestions'),
              tagline: t($messages, 'homeSuggestionsDescription'),
              icon: 'lightbulb' as const,
              active: (p: string) => p.startsWith('/suggestions'),
            },
          ]
        : []),
      ...($isAdmin
        ? [
            {
              href: '/admin',
              label: t($messages, 'navAdmin'),
              tagline: t($messages, 'adminSubtitle'),
              icon: 'admin' as const,
              active: (p: string) => p.startsWith('/admin'),
            },
          ]
        : []),
    ] as const,
  )

  const dateline = $derived(
    new Date().toLocaleDateString(undefined, {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    }),
  )

  const onArticle = $derived($pathOnly.startsWith('/news/articles/'))
  const onSearch = $derived($pathOnly === '/search' || $pathOnly.startsWith('/search/'))
  const isDark = $derived($resolvedTheme === 'dark')

  function go(href: string) {
    drawerOpen = false
    appsOpen = false
    localeOpen = false
    navigate(href)
  }

  function shortAddr(a: string) {
    return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
  }

  function closePopovers() {
    appsOpen = false
    localeOpen = false
  }

  // No full-screen backdrop: it sat in a separate stacking context and stole
  // clicks from the overflowing language/apps menus. Close on outside press.
  $effect(() => {
    if (!appsOpen && !localeOpen) return
    const onPointerDown = (e: PointerEvent) => {
      const node = e.target
      if (!(node instanceof Node)) return
      if (appsOpen && appsWrapEl?.contains(node)) return
      if (localeOpen && localeWrapEl?.contains(node)) return
      closePopovers()
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => document.removeEventListener('pointerdown', onPointerDown, true)
  })
</script>

<div class="shell">
  <header class="masthead">
    <div class="bar">
      <button
        class="icon-btn menu-btn"
        type="button"
        title={t($messages, 'navApps')}
        aria-label={t($messages, 'navApps')}
        onclick={() => {
          closePopovers()
          drawerOpen = !drawerOpen
        }}
      >
        <Icon name="menu" size={24} />
      </button>

      <a
        class="nameplate"
        href="/"
        onclick={(e) => {
          e.preventDefault()
          go('/')
        }}
      >
        <BrandMark />
        <span class="titles">
          <span class="name wide">{t($messages, 'appTitle')}</span>
          <span class="name compact">PXke</span>
          <span class="dateline">{dateline}</span>
        </span>
      </a>

      <div class="actions">
        {#if onArticle}
          <button class="back-btn wide-only-btn" type="button" onclick={() => go('/news')}>
            <Icon name="arrow_back" size={16} />
            {t($messages, 'backToFeed')}
          </button>
          <button
            class="icon-btn compact-only-btn"
            type="button"
            title={t($messages, 'backToFeed')}
            onclick={() => go('/news')}
          >
            <Icon name="arrow_back" size={20} />
          </button>
        {/if}

        <button
          class="icon-btn"
          class:active={onSearch}
          type="button"
          title={t($messages, 'navSearch')}
          aria-label={t($messages, 'navSearch')}
          onclick={() => go('/search')}
        >
          <Icon name="search" size={22} />
        </button>

        <div class="popover-wrap" bind:this={appsWrapEl}>
          <button
            class="icon-btn muted"
            type="button"
            title={t($messages, 'navApps')}
            aria-expanded={appsOpen}
            aria-haspopup="menu"
            onclick={() => {
              localeOpen = false
              appsOpen = !appsOpen
            }}
          >
            <Icon name="apps" size={22} />
          </button>
          {#if appsOpen}
            <div class="popover apps-popover" role="menu">
              <p class="popover-hint">{t($messages, 'navProductsMenuHint')}</p>
              {#each products as product}
                <button
                  type="button"
                  class="product-row"
                  class:active={product.active($pathOnly)}
                  role="menuitem"
                  onclick={() => go(product.href)}
                >
                  <span class="product-icon">
                    <Icon name={product.icon} size={20} />
                  </span>
                  <span class="product-copy">
                    <span class="product-label">
                      {product.label}
                      {#if product.active($pathOnly)}<span class="dot"></span>{/if}
                    </span>
                    <span class="product-tagline">{product.tagline}</span>
                  </span>
                </button>
              {/each}
            </div>
          {/if}
        </div>

        {#if $walletAddress}
          <button class="btn wallet" type="button" onclick={() => logout()}>
            <Icon name="wallet" size={18} class="wallet-icon" />
            <span class="wallet-label">{shortAddr($walletAddress)}</span>
          </button>
        {:else}
          <button class="btn btn-primary wallet" type="button" onclick={() => (walletOpen = true)}>
            <Icon name="wallet" size={18} class="wallet-icon" />
            <span class="wallet-label">{t($messages, 'navWallet')}</span>
          </button>
        {/if}

        <button
          class="icon-btn theme-toggle"
          type="button"
          title={isDark ? t($messages, 'themeSwitchToLight') : t($messages, 'themeSwitchToDark')}
          aria-label={isDark ? t($messages, 'themeSwitchToLight') : t($messages, 'themeSwitchToDark')}
          onclick={() => toggleLightDark()}
        >
          <Icon name={isDark ? 'light_mode' : 'dark_mode'} size={22} />
        </button>

        <div class="popover-wrap locale-wrap" bind:this={localeWrapEl}>
          <button
            class="icon-btn locale-toggle"
            type="button"
            title={t($messages, 'navLanguage')}
            aria-expanded={localeOpen}
            aria-haspopup="menu"
            onclick={() => {
              appsOpen = false
              localeOpen = !localeOpen
            }}
          >
            <Icon name="translate" size={20} />
          </button>
          {#if localeOpen}
            <div class="popover locale-popover" role="menu">
              {#each localeOptions as opt}
                <button
                  type="button"
                  class="locale-row"
                  class:selected={$localePreference === opt.value}
                  role="menuitem"
                  onclick={() => {
                    localePreference.set(opt.value)
                    localeOpen = false
                  }}
                >
                  {t($messages, opt.labelKey)}
                  {#if $localePreference === opt.value}
                    <Icon name="check" size={18} />
                  {/if}
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    </div>

    <nav class="section-nav" aria-label="Primary">
      {#each nav as item}
        <a
          class="tab"
          class:active={item.match($pathOnly)}
          href={item.href}
          onclick={(e) => {
            e.preventDefault()
            go(item.href)
          }}
        >
          {item.label}
          <span class="underline"></span>
        </a>
      {/each}
    </nav>
  </header>

  <MarketsBar />

  {#if drawerOpen}
    <div class="drawer-backdrop" onclick={() => (drawerOpen = false)} role="presentation"></div>
    <aside class="drawer" aria-label={t($messages, 'navApps')}>
      <div class="drawer-header panel">
        <BrandMark size={36} />
        <div>
          <strong>{t($messages, 'appTitle')}</strong>
          <p class="subtle">{t($messages, 'appTagline')}</p>
        </div>
      </div>

      <p class="drawer-label">{t($messages, 'navApps')}</p>
      {#each products as product}
        <button
          type="button"
          class="drawer-link"
          class:selected={product.active($pathOnly)}
          onclick={() => go(product.href)}
        >
          <Icon name={product.icon} size={21} />
          {product.label}
        </button>
      {/each}

      <p class="drawer-label">{t($messages, 'navNews')}</p>
      {#each nav as item}
        <button
          type="button"
          class="drawer-link"
          class:selected={item.match($pathOnly)}
          onclick={() => go(item.href)}
        >
          <Icon name={item.icon} size={21} />
          {item.label}
        </button>
      {/each}

      <p class="drawer-label">{t($messages, 'navAppearance')}</p>
      <button
        type="button"
        class="drawer-link"
        class:selected={$themeMode === 'light'}
        onclick={() => themeMode.set('light')}
      >
        <Icon name="light_mode" size={21} />
        {t($messages, 'themeLight')}
        {#if $themeMode === 'light'}<Icon name="check" size={18} class="trail" />{/if}
      </button>
      <button
        type="button"
        class="drawer-link"
        class:selected={$themeMode === 'dark'}
        onclick={() => themeMode.set('dark')}
      >
        <Icon name="dark_mode" size={21} />
        {t($messages, 'themeDark')}
        {#if $themeMode === 'dark'}<Icon name="check" size={18} class="trail" />{/if}
      </button>
      <button
        type="button"
        class="drawer-link"
        class:selected={$themeMode === 'system'}
        onclick={() => themeMode.set('system')}
      >
        <Icon name="brightness_auto" size={21} />
        {t($messages, 'themeSystem')}
        {#if $themeMode === 'system'}<Icon name="check" size={18} class="trail" />{/if}
      </button>

      <p class="drawer-label">{t($messages, 'navLanguage')}</p>
      {#each localeOptions as opt}
        <button
          type="button"
          class="drawer-link"
          class:selected={$localePreference === opt.value}
          onclick={() => {
            localePreference.set(opt.value)
            drawerOpen = false
          }}
        >
          {t($messages, opt.labelKey)}
          {#if $localePreference === opt.value}<Icon name="check" size={18} class="trail" />{/if}
        </button>
      {/each}
    </aside>
  {/if}

  <main>
    {@render children()}
  </main>

  <SiteFooter />
</div>

{#if walletOpen && WalletDialog}
  <WalletDialog onclose={() => (walletOpen = false)} />
{/if}

<style>
  .shell {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--surface);
  }
  .masthead {
    position: sticky;
    top: 0;
    z-index: 40;
    background: var(--app-bar);
  }
  .bar {
    height: 64px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 8px 0 4px;
    border-bottom: 1px solid var(--border);
  }
  @media (min-width: 860px) {
    .bar {
      padding: 0 12px 0 24px;
      border-bottom: 0;
    }
  }
  .menu-btn {
    display: inline-grid;
  }
  .nameplate {
    display: flex;
    align-items: center;
    gap: 12px;
    color: inherit;
    text-decoration: none;
    border-radius: 8px;
    min-width: 0;
  }
  .nameplate:hover {
    text-decoration: none;
  }
  .titles {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .name {
    font-family: var(--font-display);
    font-weight: 800;
    letter-spacing: -0.4px;
    font-size: 20px;
    line-height: 1.1;
  }
  @media (min-width: 520px) {
    .name {
      font-size: 25px;
    }
  }
  .name.wide {
    display: none;
  }
  .name.compact {
    display: block;
  }
  @media (min-width: 520px) {
    .name.wide {
      display: block;
    }
    .name.compact {
      display: none;
    }
  }
  .dateline {
    display: none;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
    color: var(--subtle);
    margin-top: 2px;
  }
  @media (min-width: 860px) {
    .dateline {
      display: block;
    }
  }
  .actions {
    margin-inline-start: auto;
    display: flex;
    align-items: center;
    gap: 2px;
    padding-inline-end: 4px;
  }
  @media (min-width: 520px) {
    .actions {
      gap: 4px;
      padding-inline-end: 12px;
    }
  }
  .icon-btn {
    border: 0;
    background: transparent;
    color: var(--on-surface);
    border-radius: 50%;
    width: 40px;
    height: 40px;
    display: inline-grid;
    place-items: center;
    flex-shrink: 0;
  }
  .icon-btn:hover {
    background: color-mix(in srgb, var(--on-surface) 8%, transparent);
  }
  .icon-btn.muted {
    color: var(--muted);
  }
  .icon-btn.active {
    color: var(--primary);
  }
  /* After .icon-btn so display:none wins over inline-grid on wide. */
  @media (min-width: 860px) {
    .menu-btn {
      display: none;
    }
  }
  .theme-toggle {
    display: none;
  }
  @media (min-width: 520px) {
    .theme-toggle {
      display: inline-grid;
    }
  }
  .locale-wrap {
    display: none;
  }
  @media (min-width: 860px) {
    .locale-wrap {
      display: block;
    }
  }
  .back-btn {
    display: none;
    align-items: center;
    gap: 6px;
    height: 36px;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: transparent;
    color: var(--on-surface);
    font-size: 13px;
    font-weight: 600;
  }
  .back-btn:hover {
    background: var(--accent-soft);
  }
  .wide-only-btn {
    display: none;
  }
  .compact-only-btn {
    display: inline-grid;
  }
  @media (min-width: 520px) {
    .wide-only-btn {
      display: inline-flex;
    }
    .compact-only-btn {
      display: none;
    }
  }
  .wallet {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    font-size: 13px;
  }
  @media (min-width: 520px) {
    .wallet {
      padding: 8px 14px;
    }
  }
  .wallet :global(.wallet-icon) {
    flex-shrink: 0;
  }
  .wallet-label {
    display: none;
  }
  @media (min-width: 520px) {
    .wallet-label {
      display: inline;
    }
  }
  .popover-wrap {
    position: relative;
    z-index: 42;
  }
  .popover {
    position: absolute;
    top: calc(100% + 8px);
    inset-inline-end: 0;
    z-index: 43;
    min-width: 280px;
    max-width: min(320px, 92vw);
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--panel);
    box-shadow: 0 12px 32px var(--card-hover-shadow);
  }
  .popover-hint {
    margin: 0;
    padding: 8px 10px 6px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .product-row {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    width: 100%;
    text-align: start;
    border: 0;
    background: transparent;
    border-radius: 12px;
    padding: 10px;
    color: var(--on-surface);
  }
  .product-row:hover,
  .product-row.active {
    background: color-mix(in srgb, var(--primary) 12%, transparent);
  }
  .product-icon {
    width: 38px;
    height: 38px;
    border-radius: 10px;
    display: grid;
    place-items: center;
    flex-shrink: 0;
    background: var(--accent-soft);
    color: var(--accent);
  }
  .product-row.active .product-icon {
    color: var(--primary);
    background: color-mix(in srgb, var(--primary) 22%, transparent);
  }
  .product-copy {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .product-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 14px;
    font-weight: 700;
  }
  .product-row.active .product-label {
    color: var(--primary);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--primary);
  }
  .product-tagline {
    font-size: 12px;
    line-height: 1.35;
    color: var(--muted);
  }
  .locale-popover {
    min-width: 220px;
    padding: 6px;
  }
  .locale-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    text-align: start;
    border: 0;
    background: transparent;
    border-radius: 10px;
    padding: 10px 12px;
    color: var(--on-surface);
    font-weight: 500;
  }
  .locale-row:hover,
  .locale-row.selected {
    background: color-mix(in srgb, var(--primary) 14%, transparent);
    font-weight: 600;
  }
  .section-nav {
    display: none;
    height: 45px;
    align-items: stretch;
    gap: 4px;
    padding: 0 18px;
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
  }
  @media (min-width: 860px) {
    .section-nav {
      display: flex;
    }
  }
  .tab {
    position: relative;
    display: inline-flex;
    align-items: center;
    padding: 0 13px;
    color: var(--muted);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.3px;
    text-decoration: none;
  }
  .tab:hover {
    color: var(--on-surface);
    text-decoration: none;
  }
  .tab.active {
    color: var(--primary);
    font-weight: 700;
  }
  .underline {
    position: absolute;
    left: 50%;
    bottom: 0;
    width: 0;
    height: 2.5px;
    border-radius: 2px;
    background: var(--primary);
    transform: translateX(-50%);
    transition: width 0.2s ease;
  }
  .tab:hover .underline {
    width: 12px;
  }
  .tab.active .underline {
    width: 22px;
  }
  .drawer-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.35);
    z-index: 50;
  }
  .drawer {
    position: fixed;
    top: 0;
    inset-inline-start: 0;
    width: min(320px, 92vw);
    height: 100%;
    z-index: 51;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 8px;
    overflow: auto;
    background: var(--panel);
    border-inline-end: 1px solid var(--border);
  }
  .drawer-header {
    display: flex;
    gap: 12px;
    align-items: center;
    margin: 4px;
    border-radius: 14px;
    background: var(--surface);
  }
  .drawer-header p {
    margin: 4px 0 0;
    font-size: 12px;
  }
  .drawer-label {
    margin: 12px 0 4px;
    padding: 0 12px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .drawer-link {
    display: flex;
    align-items: center;
    gap: 12px;
    text-align: start;
    border: 0;
    background: transparent;
    padding: 10px 12px;
    border-radius: 10px;
    color: var(--on-surface);
    font-weight: 500;
  }
  .drawer-link :global(.trail) {
    margin-inline-start: auto;
    color: var(--primary);
  }
  .drawer-link:hover,
  .drawer-link.selected {
    background: color-mix(in srgb, var(--primary) 18%, transparent);
    font-weight: 700;
  }
</style>
