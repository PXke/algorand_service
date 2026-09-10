<script lang="ts">
  import AppShell from './components/AppShell.svelte'
  import { route, matchPath, navigate } from './lib/router'
  import { splitLocalePath } from './lib/paths'
  import { config } from './lib/config'
  import { recoverFromStaleChunk, clearStaleChunkGuard } from './lib/staleChunk'
  import Home from './routes/Home.svelte'
  import NotFound from './routes/NotFound.svelte'
  import MarketplaceOverview from './routes/marketplace/Overview.svelte'
  import type { Component } from 'svelte'

  // Legacy Google-indexed /section/:slug URLs → /topic/:tag (client replace).
  const SECTION_REDIRECTS: Record<string, string> = {
    markets: 'market',
    security: 'breaking',
    developers: 'sdk',
    community: 'community',
    ecosystem: 'ecosystem',
  }

  type View =
    | { name: 'home' }
    | { name: 'news' }
    | { name: 'article'; id: string }
    | { name: 'hot'; rank: 'hot' | 'top' }
    | { name: 'topics' }
    | { name: 'topic'; tag: string }
    | { name: 'glossary' }
    | { name: 'glossaryTerm'; slug: string }
    | { name: 'registry' }
    | { name: 'registryEntry'; slug: string }
    | { name: 'registrySubmit' }
    | { name: 'registryRequest'; slug: string }
    | { name: 'search' }
    | { name: 'about' }
    | { name: 'contact' }
    | { name: 'suggestions' }
    | { name: 'admin' }
    | { name: 'shared'; token: string }
    | { name: 'notfound' }

  type LazyName =
    | 'news'
    | 'article'
    | 'hot'
    | 'topics'
    | 'topic'
    | 'glossary'
    | 'glossaryTerm'
    | 'registry'
    | 'registryEntry'
    | 'registrySubmit'
    | 'registryRequest'
    | 'search'
    | 'about'
    | 'contact'
    | 'suggestions'
    | 'admin'
    | 'shared'

  const loaders: Record<LazyName, () => Promise<{ default: Component<any> }>> = {
    news: () => import('./routes/News.svelte'),
    article: () => import('./routes/Article.svelte'),
    hot: () => import('./routes/Hot.svelte'),
    topics: () => import('./routes/Topics.svelte'),
    topic: () => import('./routes/Topic.svelte'),
    glossary: () => import('./routes/Glossary.svelte'),
    glossaryTerm: () => import('./routes/GlossaryTerm.svelte'),
    registry: () => import('./routes/Registry.svelte'),
    registryEntry: () => import('./routes/RegistryEntry.svelte'),
    registrySubmit: () => import('./routes/RegistrySubmit.svelte'),
    registryRequest: () => import('./routes/RegistryRequest.svelte'),
    search: () => import('./routes/Search.svelte'),
    about: () => import('./routes/About.svelte'),
    contact: () => import('./routes/Contact.svelte'),
    suggestions: () => import('./routes/Suggestions.svelte'),
    admin: () => import('./routes/admin/AdminHub.svelte'),
    shared: () => import('./routes/SharedArticle.svelte'),
  }

  let lazy = $state<Partial<Record<LazyName, Component<any>>>>({})

  // News-build route table. config.product === 'news' never touches the
  // marketplace view table further down; this function only runs at all
  // when config.product !== 'marketplace' (see the `view` derived below).
  function resolveView(path: string): View {
    if (path === '/') return { name: 'home' }
    if (path === '/news') return { name: 'news' }
    // Deliberately matched on the raw path, not run through splitLocalePath
    // below -- a share link is a one-off token URL, not a canonical,
    // locale-prefixed SEO route.
    const shared = matchPath('/shared/:token', path)
    if (shared) return { name: 'shared', token: shared.token }
    // Locale-prefixed article URLs (/fr/news/articles/x) resolve to the same
    // view — the locale itself is applied via localePreference on boot, so the
    // segment only needs stripping here, not routing on.
    const { rest } = splitLocalePath(path)
    const article = matchPath('/news/articles/:articleId', rest)
    if (article) return { name: 'article', id: article.articleId }
    if (path === '/hot') return { name: 'hot', rank: 'hot' }
    if (path === '/top') return { name: 'hot', rank: 'top' }
    if (path === '/topics') return { name: 'topics' }
    const topic = matchPath('/topic/:tag', path)
    if (topic) return { name: 'topic', tag: topic.tag }
    if (path === '/glossary') return { name: 'glossary' }
    const glossaryTerm = matchPath('/glossary/:slug', path)
    if (glossaryTerm) return { name: 'glossaryTerm', slug: glossaryTerm.slug }
    if (path === '/registry') return { name: 'registry' }
    // Checked before the generic /registry/:slug match below (special path
    // before the param route).
    if (path === '/registry/submit') return { name: 'registrySubmit' }
    const registryRequest = matchPath('/registry/:slug/request', path)
    if (registryRequest) return { name: 'registryRequest', slug: registryRequest.slug }
    const registryEntry = matchPath('/registry/:slug', path)
    if (registryEntry) return { name: 'registryEntry', slug: registryEntry.slug }
    const section = matchPath('/section/:slug', path)
    if (section) {
      const tag = SECTION_REDIRECTS[section.slug.toLowerCase()]
      queueMicrotask(() => navigate(tag ? `/topic/${tag}` : '/topics', true))
      return { name: 'topics' }
    }
    if (path === '/search') return { name: 'search' }
    if (path === '/about') return { name: 'about' }
    if (path === '/contact') return { name: 'contact' }
    if (path === '/suggestions') {
      if (!config.suggestionsEnabled) {
        queueMicrotask(() => navigate('/', true))
        return { name: 'home' }
      }
      return { name: 'suggestions' }
    }
    if (path === '/x402' || path.startsWith('/x402/')) {
      // The x402 storefront lives on its own domain (x402.pxke.me); nginx
      // 301s these paths there in prod, this keeps a dev server without
      // nginx landing somewhere real too. A full navigation, not
      // client-side routing -- separate SPA, separate origin.
      queueMicrotask(() => window.location.replace(config.marketplaceSiteUrl))
      return { name: 'home' }
    }
    if (path === '/admin' || path === '/sources') {
      if (path === '/sources') queueMicrotask(() => navigate('/admin', true))
      return { name: 'admin' }
    }
    return { name: 'notfound' }
  }

  // Registry build (algorand-registry.pxke.me): unlike the marketplace
  // build, the registry doesn't get its own parallel route table -- its
  // three routes (/registry, /registry/submit, /registry/:slug) already live
  // in the shared resolveView above with no product-specific prefix, so an
  // old algorand.pxke.me/registry/... link just works unchanged on the new
  // domain too. This only redirects the *root* to the listing and fences off
  // every other page (news, x402, admin, ...) back to it.
  const REGISTRY_VIEWS = new Set<View['name']>([
    'registry',
    'registryEntry',
    'registrySubmit',
    'registryRequest',
    'notfound',
  ])

  // News build (and the registry post-processing just above) run resolveView
  // directly. The marketplace build never runs resolveView -- see the
  // Marketplace route table below, which is its own separate route space
  // with its own root, not a post-processed view of this one.
  const view = $derived.by((): View => {
    const resolved = resolveView($route.path)
    if (config.product !== 'registry') return resolved
    if (resolved.name === 'home') return { name: 'registry' }
    if (!REGISTRY_VIEWS.has(resolved.name)) {
      queueMicrotask(() => navigate('/', true))
      return { name: 'registry' }
    }
    return resolved
  })

  $effect(() => {
    if (config.product === 'marketplace') return
    const name = view.name
    if (name === 'home' || name === 'notfound') return
    const key = name as LazyName
    if (lazy[key] || !loaders[key]) return
    void loaders[key]()
      .then((m) => {
        lazy = { ...lazy, [key]: m.default }
        clearStaleChunkGuard()
      })
      .catch(() => {
        void recoverFromStaleChunk()
      })
  })

  // ---- Marketplace build route table (x402.pxke.me) ---------------------
  // A separate, flat route table -- no /x402 prefix, because the storefront
  // domain IS the whole site here. One page per product (Scan, Storage,
  // News), the Overview, and Developers.
  type MarketplaceView =
    | { name: 'mktOverview' }
    | { name: 'mktScan' }
    | { name: 'mktStorage' }
    | { name: 'mktNews' }
    | { name: 'mktDevelopers' }
    | { name: 'notfound' }

  type MarketplaceLazyName = 'mktScan' | 'mktStorage' | 'mktNews' | 'mktDevelopers'

  const marketplaceLoaders: Record<MarketplaceLazyName, () => Promise<{ default: Component<any> }>> = {
    mktScan: () => import('./routes/marketplace/Scan.svelte'),
    mktStorage: () => import('./routes/marketplace/Storage.svelte'),
    mktNews: () => import('./routes/marketplace/News.svelte'),
    mktDevelopers: () => import('./routes/marketplace/Developers.svelte'),
  }

  let mktLazy = $state<Partial<Record<MarketplaceLazyName, Component<any>>>>({})

  // Paths earlier builds of x402.pxke.me served (the old /x402-prefixed tab
  // routing, then the 2026-09-07 directory/board/requests/list pages) --
  // kept as client-side redirects so a link already shared from either
  // era lands on the overview instead of a dead end.
  const MARKETPLACE_LEGACY_REDIRECTS = new Set([
    '/x402',
    '/directory',
    '/listing',
    '/requests',
    '/list',
    '/board',
    '/trust',
    '/services',
  ])

  function resolveMarketplaceView(path: string): MarketplaceView {
    if (MARKETPLACE_LEGACY_REDIRECTS.has(path) || path.startsWith('/x402/')) {
      queueMicrotask(() => navigate('/', true))
      return { name: 'mktOverview' }
    }
    if (path === '/') return { name: 'mktOverview' }
    if (path === '/scan') return { name: 'mktScan' }
    if (path === '/storage') return { name: 'mktStorage' }
    if (path === '/news') return { name: 'mktNews' }
    if (path === '/developers') return { name: 'mktDevelopers' }
    return { name: 'notfound' }
  }

  const mktView = $derived.by((): MarketplaceView =>
    config.product === 'marketplace'
      ? resolveMarketplaceView($route.path)
      : { name: 'mktOverview' },
  )

  $effect(() => {
    if (config.product !== 'marketplace') return
    const name = mktView.name
    if (name === 'mktOverview' || name === 'notfound') return
    const key = name as MarketplaceLazyName
    if (mktLazy[key] || !marketplaceLoaders[key]) return
    void marketplaceLoaders[key]()
      .then((m) => {
        mktLazy = { ...mktLazy, [key]: m.default }
        clearStaleChunkGuard()
      })
      .catch(() => {
        void recoverFromStaleChunk()
      })
  })
</script>

<AppShell>
  {#if config.product === 'marketplace'}
    {#if mktView.name === 'mktOverview'}
      <MarketplaceOverview />
    {:else if mktView.name === 'notfound'}
      <NotFound />
    {:else if mktLazy[mktView.name]}
      {@const C = mktLazy[mktView.name]!}
      <C />
    {:else}
      <div class="page"><p class="muted">Loading…</p></div>
    {/if}
  {:else if view.name === 'home'}
    <Home />
  {:else if view.name === 'notfound'}
    <NotFound />
  {:else if lazy[view.name]}
    {#if view.name === 'article'}
      {#key view.id}
        {@const C = lazy.article!}
        <C articleId={view.id} />
      {/key}
    {:else if view.name === 'hot'}
      {#key view.rank}
        {@const C = lazy.hot!}
        <C rank={view.rank} />
      {/key}
    {:else if view.name === 'topic'}
      {#key view.tag}
        {@const C = lazy.topic!}
        <C tag={view.tag} />
      {/key}
    {:else if view.name === 'glossaryTerm'}
      {#key view.slug}
        {@const C = lazy.glossaryTerm!}
        <C slug={view.slug} />
      {/key}
    {:else if view.name === 'registryEntry'}
      {#key view.slug}
        {@const C = lazy.registryEntry!}
        <C slug={view.slug} />
      {/key}
    {:else if view.name === 'registryRequest'}
      {#key view.slug}
        {@const C = lazy.registryRequest!}
        <C slug={view.slug} />
      {/key}
    {:else if view.name === 'shared'}
      {#key view.token}
        {@const C = lazy.shared!}
        <C token={view.token} />
      {/key}
    {:else}
      {@const C = lazy[view.name]!}
      <C />
    {/if}
  {:else}
    <div class="page"><p class="muted">Loading…</p></div>
  {/if}
</AppShell>
