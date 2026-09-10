<script lang="ts">
  import { messages, t, localePreference, localeOptions, activeLocale } from '../lib/i18n'
  import { themeMode, resolvedTheme, toggleLightDark } from '../lib/theme'
  import { walletAddress, isAdmin, logout } from '../lib/auth/session'
  import { setAnalyticsOptOut } from '../lib/analyticsOptOut'
  import { config } from '../lib/config'
  import { navigate, pathOnly } from '../lib/router'
  import { articleChromeCollapsed } from '../lib/articleChrome'
  import { recoverFromStaleChunk, clearStaleChunkGuard } from '../lib/staleChunk'
  import BrandMark from './BrandMark.svelte'
  import Icon from './Icon.svelte'
  import MarketsBar from './MarketsBar.svelte'
  import SiteFooter from './SiteFooter.svelte'
  import { liveClock, formatDateline } from '../lib/liveClock'
  import type { Component } from 'svelte'

  let { children }: { children: import('svelte').Snippet } = $props()

  let drawerOpen = $state(false)
  let walletOpen = $state(false)
  let WalletDialog = $state<Component<{ onclose: () => void }> | null>(null)
  let appsOpen = $state(false)
  let localeOpen = $state(false)
  let appsWrapEl = $state<HTMLElement | null>(null)
  let localeWrapEl = $state<HTMLElement | null>(null)

  /* Admin wallet → pxke_no_track cookie so our visits aren't counted.
     Only ever SETS it. $isAdmin is false while the wallet session is still
     being restored, so calling setAnalyticsOptOut($isAdmin) unconditionally
     deleted the cookie on every single page load and only re-set it once the
     wallet resolved — leaving a window (and a hard refresh) where our own
     visits were counted. Clearing is now explicit, on logout. */
  $effect(() => {
    if ($isAdmin) setAnalyticsOptOut(true)
  })

  $effect(() => {
    if (!walletOpen || WalletDialog) return
    void import('./WalletDialog.svelte')
      .then((m) => {
        WalletDialog = m.default
        clearStaleChunkGuard()
      })
      .catch((e) => {
        console.error('Failed to load WalletDialog', e)
        void recoverFromStaleChunk().then((reloading) => {
          if (!reloading) walletOpen = false
        })
      })
  })

  /* The masthead tab row carries content destinations only. Top, About and
     Contact still exist — they live in the drawer and the footer — but three
     near-identical feed tabs plus two marketing pages made the row read as
     noise.

     Marketplace build (config.product === 'marketplace', x402.pxke.me): the
     storefront's own pages sit in this same tab row -- one per product plus
     Developers -- no newspaper chrome, no second nav component. Static
     routes, so the nav never depends on the catalog fetch. */
  const MARKETPLACE_NAV_PAGES = [
    {
      href: '/scan',
      labelKey: 'x402ProductScanName',
      icon: 'search' as const,
      match: (p: string): boolean => p === '/scan',
    },
    {
      href: '/storage',
      labelKey: 'x402ProductStorageName',
      icon: 'layers' as const,
      match: (p: string): boolean => p === '/storage',
    },
    {
      href: '/news',
      labelKey: 'x402ProductNewsName',
      icon: 'article' as const,
      match: (p: string): boolean => p === '/news',
    },
  ] as const

  // Registry build (algorand-registry.pxke.me): a single-purpose site, so
  // one tab -- Browse (the listing itself, filters live inline on the page,
  // there's no separate /registry/categories route) -- with "Submit a
  // project" as the one secondary CTA in moreNav below. No newspaper chrome,
  // no marketplace/x402 chrome, no social -- plain literals per the same
  // "not a new i18n key" convention as the marketplace build above.
  const REGISTRY_NAV_PAGES = [
    {
      href: '/registry',
      label: 'Browse',
      icon: 'hub' as const,
      match: (p: string) =>
        p === '/' || p === '/registry' || (p.startsWith('/registry/') && p !== '/registry/submit'),
    },
  ] as const

  const sections = $derived(
    config.product === 'marketplace'
      ? MARKETPLACE_NAV_PAGES.map((entry) => ({
          href: entry.href,
          label: t($messages, entry.labelKey),
          icon: entry.icon,
          match: entry.match,
        }))
      : config.product === 'registry'
      ? REGISTRY_NAV_PAGES
      : [
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
        ],
  )

  const moreNav = $derived(
    config.product === 'marketplace'
      ? [
          {
            href: '/developers',
            label: t($messages, 'x402DevTitle'),
            icon: 'article' as const,
            match: (p: string) => p === '/developers',
          },
        ]
      : config.product === 'registry'
      ? [
          {
            href: '/registry/submit',
            label: 'Submit a project',
            icon: 'send' as const,
            match: (p: string) => p === '/registry/submit',
          },
        ]
      : [
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
          {
            // The x402 storefront is its own site (x402.pxke.me): a real
            // cross-origin navigation from the drawer, never go()/navigate().
            href: config.marketplaceSiteUrl,
            label: t($messages, 'navX402'),
            icon: 'hub' as const,
            match: (_p: string): boolean => false,
            external: true,
          },
        ],
  )

  const drawerNav = $derived([...sections, ...moreNav])

  // The product switcher. On the News build it lists News/Marketplace (+
  // Suggestions/Admin) as in-app SPA routes. On the Marketplace build it is
  // just Marketplace (self) + News -- but News is a DIFFERENT domain
  // (algorand.pxke.me), a real cross-origin navigation, not client-side
  // routing -- hence `external`, rendered as a plain <a>, never go()/navigate().
  // Registry entry, shared by whichever build ISN'T the registry build --
  // always external (a separate domain/SPA either way).
  const registrySwitcherEntry = {
    href: config.registrySiteUrl,
    label: 'Registry',
    tagline: 'A free, human-reviewed directory of Algorand projects.',
    icon: 'hub' as const,
    active: (_p: string): boolean => false,
    external: true,
  } as const

  const products = $derived(
    config.product === 'marketplace'
      ? ([
          {
            href: '/',
            label: t($messages, 'navX402'),
            tagline: t($messages, 'x402ProductTagline'),
            icon: 'hub' as const,
            active: (_p: string): boolean => true,
            external: false,
          },
          {
            href: config.newsSiteUrl,
            label: t($messages, 'navNews'),
            tagline: t($messages, 'homeNewsDescription'),
            icon: 'menu_book' as const,
            active: (_p: string): boolean => false,
            external: true,
          },
          registrySwitcherEntry,
        ] as const)
      : config.product === 'registry'
      ? // Registry build: a small, single-purpose site, but still one of
        // three sibling products -- News and Marketplace need a way out
        // (operator ask 2026-09-07: "no menu to go to news/x402?").
        ([
          {
            href: '/',
            label: 'PXke Registry',
            tagline: 'A free, human-reviewed directory of Algorand projects.',
            icon: 'hub' as const,
            active: (_p: string): boolean => true,
            external: false,
          },
          {
            href: config.newsSiteUrl,
            label: t($messages, 'navNews'),
            tagline: t($messages, 'homeNewsDescription'),
            icon: 'menu_book' as const,
            active: (_p: string): boolean => false,
            external: true,
          },
          {
            href: config.marketplaceSiteUrl,
            label: t($messages, 'navX402'),
            tagline: t($messages, 'x402ProductTagline'),
            icon: 'hub' as const,
            active: (_p: string): boolean => false,
            external: true,
          },
        ] as const)
      : ([
          {
            href: '/',
            label: t($messages, 'navNews'),
            tagline: t($messages, 'homeNewsDescription'),
            icon: 'menu_book' as const,
            active: (p: string): boolean =>
              p === '/' ||
              p.startsWith('/news') ||
              p === '/hot' ||
              p === '/top' ||
              p === '/topics' ||
              p.startsWith('/topic/') ||
              p === '/about' ||
              p === '/contact',
            external: false,
          },
          {
            // Cross-origin since 2026-09-07 (x402.pxke.me split out) -- /x402
            // still 301s here for old links/bookmarks, but the switcher
            // itself should send a fresh click straight to the real domain,
            // not through a redirect hop.
            href: config.marketplaceSiteUrl,
            label: t($messages, 'navX402'),
            tagline: t($messages, 'x402ProductTagline'),
            icon: 'hub' as const,
            active: (_p: string): boolean => false,
            external: true,
          },
          registrySwitcherEntry,
          ...(config.suggestionsEnabled
            ? [
                {
                  href: '/suggestions',
                  label: t($messages, 'navSuggestions'),
                  tagline: t($messages, 'homeSuggestionsDescription'),
                  icon: 'lightbulb' as const,
                  active: (p: string): boolean => p.startsWith('/suggestions'),
                  external: false,
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
                  active: (p: string): boolean => p.startsWith('/admin'),
                  external: false,
                },
              ]
            : []),
        ] as const),
  )

  const dateline = $derived(formatDateline($activeLocale))
  const datelineShort = $derived(formatDateline($activeLocale, true))
  const onArticle = $derived($pathOnly.startsWith('/news/articles/'))
  const onSearch = $derived($pathOnly === '/search' || $pathOnly.startsWith('/search/'))
  const isDark = $derived($resolvedTheme === 'dark')
  const readingCollapsed = $derived(onArticle && $articleChromeCollapsed)

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

  $effect(() => {
    if (!drawerOpen) return
    const prevOverflow = document.body.style.overflow
    const prevPad = document.body.style.paddingRight
    const sb = window.innerWidth - document.documentElement.clientWidth
    document.body.style.overflow = 'hidden'
    if (sb > 0) document.body.style.paddingRight = `${sb}px`
    return () => {
      document.body.style.overflow = prevOverflow
      document.body.style.paddingRight = prevPad
    }
  })
</script>

<div
  class="shell"
  class:on-article={onArticle}
  class:reading-collapsed={readingCollapsed}
>
  <!-- Keyboard users otherwise tab the masthead, section nav and every
       control before reaching the story on every single page. -->
  <a class="skip-link" href="#main">{t($messages, 'skipToContent')}</a>

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
        <BrandMark size={34} />
        <span class="titles">
          <!-- Marketplace/registry builds: masthead word changes, everything
               else (brand mark, dateline, layout) stays the shared design
               system -- plain literals, not new i18n keys, matching how the
               compact "PXke" fallback below was already untranslated. -->
          <span class="name wide">
            {config.product === 'marketplace'
              ? 'PXke x402'
              : config.product === 'registry'
              ? 'PXke Registry'
              : t($messages, 'appTitle')}
          </span>
          <span class="name compact"
            >{config.product === 'marketplace'
              ? 'x402'
              : config.product === 'registry'
              ? 'Registry'
              : 'PXke'}</span
          >
          {#if config.product === 'news'}
            <span class="dateline">
              <span class="date-long">{dateline}</span>
              <span class="date-short">{datelineShort}</span>
              <span class="clock-pair">
                <span class="clock-sep" aria-hidden="true">·</span>
                <span class="clock" {@attach liveClock($activeLocale)}></span>
              </span>
            </span>
          {:else}
            <!-- The edition stamp is newspaper chrome. A marketplace or a
                 directory says what it is instead. -->
            <span class="tagline">
              {config.product === 'marketplace'
                ? t($messages, 'x402Masthead')
                : 'Algorand projects, human-reviewed'}
            </span>
          {/if}
        </span>
      </a>

      <!-- In the bar, not a strip of its own: on the front page the bar held a
           28px mark at the far left and four icons at the far right with ~1000px
           of empty paper between them, then the tabs sat alone on a second thin
           strip below. One row does both jobs. -->
      <nav class="section-nav" aria-label="Primary">
        {#each sections as item (item.href)}
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

      <div class="actions">
        {#if onArticle}
          <button class="back-btn" type="button" onclick={() => go('/news')}>
            <span class="chevron" aria-hidden="true"></span>
            {t($messages, 'navLatest')}
          </button>
        {/if}

        {#if config.product === 'news'}
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
        {:else}
          <!-- The marketplace and registry builds have no /search route; their
               one call to action takes the search icon's place. -->
          {#each moreNav.slice(0, 1) as cta (cta.href)}
            <a
              class="masthead-cta"
              class:active={cta.match($pathOnly)}
              href={cta.href}
              onclick={(e) => {
                e.preventDefault()
                go(cta.href)
              }}
            >
              {cta.label}
            </a>
          {/each}
        {/if}

        <!-- Suggestions is config-gated and Admin is wallet-gated, so for an
             ordinary reader this menu held exactly one entry: the page they
             were already on. Only show a switcher when there's a choice. -->
        {#if products.length > 1}
        <div class="popover-wrap article-secondary" bind:this={appsWrapEl}>
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
              {#each products as product (product.href)}
                {#if product.external}
                  <!-- Cross-domain product (News, from the Marketplace build): a
                       real navigation, never go()/navigate() -- the two builds
                       are separate SPAs on separate origins. -->
                  <a
                    class="product-row"
                    href={product.href}
                    role="menuitem"
                    onclick={closePopovers}
                  >
                    <span class="product-copy">
                      <span class="product-label">{product.label}</span>
                      <span class="product-tagline">{product.tagline}</span>
                    </span>
                  </a>
                {:else}
                  <button
                    type="button"
                    class="product-row"
                    class:active={product.active($pathOnly)}
                    role="menuitem"
                    onclick={() => go(product.href)}
                  >
                    <span class="product-copy">
                      <span class="product-label">{product.label}</span>
                      <span class="product-tagline">{product.tagline}</span>
                    </span>
                  </button>
                {/if}
              {/each}
            </div>
          {/if}
        </div>
        {/if}

        {#if $walletAddress}
          <button
            class="wallet-stamp signed article-secondary"
            type="button"
            title={t($messages, 'walletConnected')}
            onclick={() => logout()}
          >
            {shortAddr($walletAddress)}
          </button>
        {:else if config.product !== 'registry'}
          <!-- No registry action needs a connected wallet for a non-admin
               visitor (2026-09-08 Fable review): the submit flow is free/
               no-login, and the only thing a wallet unlocks anywhere on this
               site is the admin panel, which a signed-in admin already sees
               via the branch above (cross-domain session, see
               lib/auth/session.ts's restoreFromCookie). Offering a "Connect
               wallet" prompt with nothing behind it just confuses a visitor
               -- RegistrySubmit.svelte's own lead line already says "Free,
               no wallet needed" directly under where this button used to
               sit. -->
          <button
            class="wallet-stamp article-secondary"
            type="button"
            onclick={() => (walletOpen = true)}
          >
            {t($messages, 'navWallet')}
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
            aria-label={t($messages, 'navLanguage')}
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
              {#each localeOptions as opt (opt.value)}
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
                    <span class="tick" aria-hidden="true">·</span>
                  {/if}
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    </div>
  </header>

  <!-- Registry build: a single-purpose directory site, not the newspaper --
       no ALGO price/round ticker chrome. MarketsBar's own showOn already
       gates on `/`, which the registry build also uses as its root, so this
       is excluded at the call site rather than inside the shared component. -->
  {#if config.product === 'news'}
    <MarketsBar />
  {/if}

  {#if drawerOpen}
    <div class="drawer-backdrop" onclick={() => (drawerOpen = false)} role="presentation"></div>
    <aside class="drawer" aria-label={t($messages, 'navApps')}>
      <div class="drawer-header">
        <BrandMark size={30} />
        <div>
          <strong>{t($messages, 'appTitle')}</strong>
          <p class="subtle">{t($messages, 'appTagline')}</p>
        </div>
      </div>

      {#if products.length > 1}
        <p class="drawer-label">{t($messages, 'navApps')}</p>
        {#each products as product (product.href)}
          {#if product.external}
            <a class="drawer-link" href={product.href}>{product.label}</a>
          {:else}
            <button
              type="button"
              class="drawer-link"
              class:selected={product.active($pathOnly)}
              onclick={() => go(product.href)}
            >
              {product.label}
            </button>
          {/if}
        {/each}
      {/if}

      <p class="drawer-label">
        {config.product === 'marketplace'
          ? t($messages, 'navX402')
          : config.product === 'registry'
          ? 'Registry'
          : t($messages, 'navNews')}
      </p>
      {#each drawerNav as item (item.href)}
        {#if 'external' in item && item.external}
          <a class="drawer-link" href={item.href} onclick={() => (drawerOpen = false)}>
            {item.label}
          </a>
        {:else}
          <button
            type="button"
            class="drawer-link"
            class:selected={item.match($pathOnly)}
            onclick={() => go(item.href)}
          >
            {item.label}
          </button>
        {/if}
      {/each}

      <p class="drawer-label">{t($messages, 'navAppearance')}</p>
      {#if $walletAddress}
        <button type="button" class="drawer-link" onclick={() => logout()}>
          {shortAddr($walletAddress)}
        </button>
      {:else if config.product !== 'registry'}
        <!-- Same reasoning as the desktop wallet-stamp button above. -->
        <button
          type="button"
          class="drawer-link"
          onclick={() => {
            drawerOpen = false
            walletOpen = true
          }}
        >
          {t($messages, 'walletConnect')}
        </button>
      {/if}
      <button
        type="button"
        class="drawer-link"
        class:selected={$themeMode === 'light'}
        onclick={() => themeMode.set('light')}
      >
        {t($messages, 'themeLight')}
        {#if $themeMode === 'light'}<span class="tick" aria-hidden="true">·</span>{/if}
      </button>
      <button
        type="button"
        class="drawer-link"
        class:selected={$themeMode === 'dark'}
        onclick={() => themeMode.set('dark')}
      >
        {t($messages, 'themeDark')}
        {#if $themeMode === 'dark'}<span class="tick" aria-hidden="true">·</span>{/if}
      </button>
      <button
        type="button"
        class="drawer-link"
        class:selected={$themeMode === 'system'}
        onclick={() => themeMode.set('system')}
      >
        {t($messages, 'themeSystem')}
        {#if $themeMode === 'system'}<span class="tick" aria-hidden="true">·</span>{/if}
      </button>

      <p class="drawer-label">{t($messages, 'navLanguage')}</p>
      {#each localeOptions as opt (opt.value)}
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
          {#if $localePreference === opt.value}<span class="tick" aria-hidden="true">·</span>{/if}
        </button>
      {/each}
    </aside>
  {/if}

  <main id="main" tabindex="-1">
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
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    background: transparent;
  }
  /* Off-screen until focused, then pinned over the masthead. */
  .skip-link {
    position: absolute;
    top: 0;
    inset-inline-start: 0;
    z-index: 60;
    transform: translateY(-120%);
    padding: 12px 18px;
    background: var(--panel);
    color: var(--primary);
    border: 1px solid var(--border);
    border-radius: 0 0 10px 0;
    font-size: 14px;
    font-weight: 700;
    text-decoration: none;
  }
  .skip-link:focus-visible {
    transform: translateY(0);
  }
  main:focus {
    outline: none;
  }
  .masthead {
    position: sticky;
    top: 0;
    z-index: 40;
    /* Opaque, not frosted glass: at 86% the headlines underneath showed
       through the bar and read as a rendering fault. A masthead is printed
       on the paper, not floated over it — and this drops a backdrop-filter
       that was compositing the whole page on every scroll frame. */
    background: var(--masthead);
    color: var(--masthead-ink);
    border-top: 0;
    border-bottom: 1px solid var(--border);
    box-shadow: none;
  }
  :global(html[data-theme='dark']) .masthead :global(.mark) {
    background: transparent;
    color: var(--masthead-ink);
    box-shadow: none;
  }
  /* While reading mid-article: drop site chrome so only the title strip remains. */
  .shell.reading-collapsed .masthead,
  .shell.reading-collapsed :global(.markets) {
    display: none;
  }
  /* Every strip in the masthead shares the content measure and gutters —
     otherwise the mark and the tabs sit hundreds of px left of the headlines
     they sit above. --shell-gutter is declared once in app.css :root, which is
     also what .page uses, so the two cannot drift apart. */
  .bar {
    width: 100%;
    max-width: var(--max-wide);
    margin-inline: auto;
    padding-inline: var(--shell-gutter);
    padding-top: env(safe-area-inset-top, 0);
    box-sizing: border-box;
    height: calc(64px + env(safe-area-inset-top, 0px));
    display: flex;
    align-items: center;
    gap: 8px;
  }
  /* Articles on phone: menu + brand + search + language — apps/wallet in the drawer. */
  @media (max-width: 859px) {
    .shell.on-article .article-secondary {
      display: none;
    }
    .shell.on-article .bar {
      height: calc(52px + env(safe-area-inset-top, 0px));
      gap: 4px;
    }
  }
  @media (max-width: 519px) {
    .shell:not(.on-article) .article-secondary {
      display: none;
    }
  }
  @media (min-width: 860px) {
    .bar {
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
  /* Nameplate lives in the bar. Stretch matches headlines, not a condensed masthead. */
  .name {
    font-family: var(--font-display);
    font-stretch: 94%;
    font-weight: 800;
    letter-spacing: -0.5px;
    font-size: var(--fs-name);
    line-height: 1.1;
  }
  @media (min-width: 520px) {
    .name {
      font-size: 22px;
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
  /* One edition stamp for the whole site — date here, not again on the folio or footer. */
  .dateline {
    display: block;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--masthead-muted);
    margin-top: 3px;
  }
  .date-long,
  .clock-pair {
    display: none;
  }
  .date-short {
    display: inline;
  }
  .clock-sep {
    margin-inline: 6px;
    color: var(--subtle);
  }
  .clock {
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.4px;
  }
  @media (min-width: 860px) {
    .date-long,
    .clock-pair {
      display: inline;
    }
    .date-short {
      display: none;
    }
  }
  .tagline {
    display: none;
    font-family: var(--font-sans);
    font-size: 12px;
    color: var(--masthead-muted);
    margin-top: 3px;
  }
  @media (min-width: 860px) {
    .tagline {
      display: block;
    }
  }
  .masthead-cta {
    display: none;
    align-items: center;
    min-height: 34px;
    padding: 0 14px;
    margin-inline-end: 6px;
    border: 1px solid var(--masthead-ink);
    border-radius: var(--radius-control);
    color: var(--masthead-ink);
    font-family: var(--font-sans);
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    white-space: nowrap;
  }
  .masthead-cta:hover {
    background: var(--masthead-ink);
    color: var(--surface);
    text-decoration: none;
  }
  .masthead-cta.active {
    opacity: 0.55;
  }
  @media (min-width: 700px) {
    .masthead-cta {
      display: inline-flex;
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
    color: var(--masthead-ink);
    border-radius: 50%;
    width: 44px;
    height: 44px;
    display: inline-grid;
    place-items: center;
    flex-shrink: 0;
  }
  .icon-btn:hover {
    background: color-mix(in srgb, var(--masthead-ink) 12%, transparent);
  }
  .icon-btn.muted {
    color: var(--masthead-muted);
  }
  .icon-btn.active {
    color: var(--accent);
  }
  /* After .icon-btn so display:none wins over inline-grid on wide. */
  @media (min-width: 860px) {
    .menu-btn {
      display: none;
    }
  }
  .back-btn {
    display: none;
    align-items: center;
    gap: 6px;
    height: 36px;
    padding: 0 8px;
    border: 0;
    background: transparent;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--masthead-muted);
  }
  .back-btn:hover {
    color: var(--accent);
    background: transparent;
  }
  .back-btn .chevron::before {
    content: '‹';
    font-size: 16px;
    line-height: 1;
    font-weight: 400;
  }
  :global([dir='rtl']) .back-btn .chevron::before {
    content: '›';
  }
  @media (min-width: 860px) {
    .back-btn {
      display: inline-flex;
    }
  }
  .wallet-stamp {
    border: 0;
    background: transparent;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--masthead-muted);
    min-height: 44px;
    padding: 0 8px;
    flex-shrink: 0;
  }
  .wallet-stamp.signed {
    text-transform: none;
    letter-spacing: 0.2px;
    font-variant-numeric: tabular-nums;
  }
  .wallet-stamp:hover,
  .wallet-stamp:focus-visible {
    color: var(--accent);
    background: transparent;
  }
  .popover-wrap {
    position: relative;
    z-index: 42;
  }
  .popover {
    position: absolute;
    top: calc(100% + 1px);
    inset-inline-end: 0;
    z-index: 43;
    min-width: 260px;
    max-width: min(320px, 92vw);
    padding: 4px 0;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    box-shadow: none;
    animation: pop-in 0.18s ease both;
  }
  @keyframes pop-in {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .popover {
      animation: none;
    }
    .underline {
      transition: none;
    }
  }
  .popover-hint {
    margin: 0;
    padding: 10px 14px 6px;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.9px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .popover-hint::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 9px;
    background: var(--accent);
    vertical-align: 6%;
  }
  .product-row {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    width: 100%;
    text-align: start;
    border: 0;
    background: transparent;
    border-radius: 0;
    padding: 10px 14px;
    color: var(--on-surface);
  }
  .product-row:hover {
    background: transparent;
    color: var(--accent);
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
    gap: 8px;
    font-size: 14px;
    font-weight: 700;
  }
  .product-row.active .product-label {
    color: var(--accent);
  }
  .product-row.active .product-label::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    background: var(--accent);
    flex-shrink: 0;
  }
  .product-tagline {
    font-size: 12px;
    line-height: 1.35;
    color: var(--muted);
  }
  .product-row:hover .product-tagline,
  .product-row.active .product-tagline {
    color: var(--muted);
  }
  .locale-popover {
    min-width: 200px;
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
    border-radius: 0;
    padding: 10px 14px;
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 12.5px;
    font-weight: 500;
    letter-spacing: 0.2px;
  }
  .locale-row:hover {
    color: var(--accent);
    background: transparent;
  }
  .locale-row.selected {
    color: var(--accent);
    font-weight: 600;
    background: transparent;
  }
  .tick {
    color: var(--accent);
    font-family: var(--font-mono);
    font-weight: 700;
    margin-inline-start: auto;
  }
  .section-nav {
    display: flex;
    align-items: stretch;
    gap: 0;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior-x: contain;
  }
  .section-nav::-webkit-scrollbar {
    display: none;
  }
  @media (max-width: 859px) {
    /* Too tight to share the bar with the menu, the mark and the icons, so it
       wraps back onto its own line — the phone bar was never the empty one. */
    .bar {
      flex-wrap: wrap;
      height: auto;
    }
    .bar > .menu-btn,
    .bar > .nameplate,
    .bar > .actions {
      min-height: calc(64px + env(safe-area-inset-top, 0px));
    }
    .shell.on-article .bar > .menu-btn,
    .shell.on-article .bar > .nameplate,
    .shell.on-article .bar > .actions {
      min-height: calc(52px + env(safe-area-inset-top, 0px));
    }
    .section-nav {
      flex-basis: 100%;
      order: 10;
      height: 40px;
      border-top: 1px solid color-mix(in srgb, var(--masthead-ink) 14%, transparent);
    }
    .shell.on-article .section-nav {
      display: none;
    }
    .section-nav .tab {
      flex: 0 0 auto;
      padding: 0 12px;
      font-size: 12.5px;
    }
  }
  @media (min-width: 860px) {
    .section-nav {
      /* Fills the paper the bar used to leave blank. .actions already carries
         margin-inline-start:auto, which is what pins the icons to the right. */
      align-self: stretch;
      margin-inline-start: 26px;
      gap: 4px;
      overflow: visible;
    }
  }
  .tab {
    position: relative;
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 13px;
    color: var(--masthead-muted);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.3px;
    text-decoration: none;
    transition: color 0.2s ease;
  }
  .tab:hover {
    color: var(--masthead-ink);
    text-decoration: none;
  }
  .tab.active {
    color: var(--masthead-ink);
    font-weight: 700;
  }
  .underline {
    position: absolute;
    left: 50%;
    bottom: 0;
    width: 22px;
    height: 2px;
    border-radius: 0;
    background: var(--accent);
    transform: translateX(-50%) scaleX(0);
    transform-origin: center;
    transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
  }
  .tab:hover .underline {
    transform: translateX(-50%) scaleX(0.55);
  }
  .tab.active .underline {
    transform: translateX(-50%) scaleX(1);
  }
  .drawer-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.35);
    z-index: 50;
    animation: fade-in 0.2s ease both;
  }
  .drawer {
    position: fixed;
    top: 0;
    inset-inline-start: 0;
    width: min(300px, 88vw);
    height: 100%;
    height: 100dvh;
    z-index: 51;
    display: flex;
    flex-direction: column;
    gap: 0;
    padding: max(16px, env(safe-area-inset-top, 0px)) 16px
      max(16px, env(safe-area-inset-bottom, 0px));
    padding-inline-start: max(16px, env(safe-area-inset-left, 0px));
    overflow: auto;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
    background: var(--surface);
    border-inline-end: 1px solid var(--border);
    box-shadow: none;
    animation: drawer-in 0.28s ease both;
  }
  @keyframes fade-in {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
  @keyframes drawer-in {
    from {
      opacity: 0.6;
      transform: translateX(-12px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @keyframes drawer-in-rtl {
    from {
      opacity: 0.6;
      transform: translateX(12px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  :global([dir='rtl']) .drawer {
    animation-name: drawer-in-rtl;
  }
  @media (prefers-reduced-motion: reduce) {
    .drawer-backdrop,
    .drawer {
      animation: none;
    }
  }
  .drawer-header {
    display: flex;
    gap: 12px;
    align-items: center;
    margin: 0 0 8px;
    padding: 0 0 16px;
    border-bottom: 1px solid var(--border);
    background: transparent;
  }
  .drawer-header strong {
    display: block;
    font-family: var(--font-display);
    font-stretch: 94%;
    font-weight: 800;
    letter-spacing: -0.4px;
    font-size: 18px;
  }
  .drawer-header p {
    margin: 4px 0 0;
    font-size: 12px;
  }
  .drawer-label {
    margin: 18px 0 4px;
    padding: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.9px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .drawer-label::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 9px;
    background: var(--accent);
    vertical-align: 6%;
  }
  .drawer-link {
    display: flex;
    align-items: center;
    gap: 8px;
    text-align: start;
    border: 0;
    background: transparent;
    padding: 10px 0;
    min-height: 44px;
    border-radius: 0;
    color: var(--on-surface);
    font-weight: 500;
  }
  .drawer-link:hover {
    color: var(--accent);
    background: transparent;
  }
  a.drawer-link {
    text-decoration: none;
  }
  .drawer-link.selected {
    color: var(--accent);
    font-weight: 600;
    background: transparent;
  }
</style>
