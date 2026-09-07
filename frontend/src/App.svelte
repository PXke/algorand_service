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

  type X402Tab = 'directory' | 'board' | 'requests' | 'grades' | 'register'
  const X402_TABS: readonly X402Tab[] = ['directory', 'board', 'requests', 'grades', 'register']

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
    | { name: 'search' }
    | { name: 'about' }
    | { name: 'contact' }
    | { name: 'suggestions' }
    | { name: 'x402'; tab: X402Tab }
    | { name: 'x402Endpoints' }
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
    | 'search'
    | 'about'
    | 'contact'
    | 'suggestions'
    | 'x402'
    | 'x402Endpoints'
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
    search: () => import('./routes/Search.svelte'),
    about: () => import('./routes/About.svelte'),
    contact: () => import('./routes/Contact.svelte'),
    suggestions: () => import('./routes/Suggestions.svelte'),
    x402: () => import('./routes/X402.svelte'),
    x402Endpoints: () => import('./routes/X402Endpoints.svelte'),
    admin: () => import('./routes/admin/AdminHub.svelte'),
    shared: () => import('./routes/SharedArticle.svelte'),
  }

  let lazy = $state<Partial<Record<LazyName, Component<any>>>>({})

  // News-build route table. UNCHANGED by the 2026-09-07 marketplace redesign
  // below: every path here, /x402* included, still resolves exactly as it
  // did (X402.svelte/X402Endpoints.svelte, the newspaper's own /x402 hub
  // page) -- config.product === 'news' never touches the Marketplace* view
  // table further down. This function only runs at all when
  // config.product !== 'marketplace' (see the `view` derived below).
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
    // Checked before the generic /registry/:slug match below, same
    // "special path before the param route" precedent as /x402/endpoints.
    if (path === '/registry/submit') return { name: 'registrySubmit' }
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
    if (path === '/x402') return { name: 'x402', tab: 'directory' }
    // Checked before the generic /x402/:tab match below -- "endpoints" is a
    // separate top-level page (PXke's own products), not one of the
    // Marketplace page's own tabs.
    if (path === '/x402/endpoints') return { name: 'x402Endpoints' }
    const x402 = matchPath('/x402/:tab', path)
    if (x402) {
      const tab = X402_TABS.find((t) => t === x402.tab.toLowerCase())
      if (tab) return { name: 'x402', tab }
      queueMicrotask(() => navigate('/x402', true))
      return { name: 'x402', tab: 'directory' }
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
  // A genuinely separate, flat route table -- no /x402 prefix, because the
  // marketplace domain IS the whole site here, not a section of the
  // newspaper. Real top-level pages per
  // docs/x402-marketplace-product-redesign.md §4: Overview / Directory /
  // Listing detail / Board / Requests / Trust / Services / Developers /
  // List an endpoint, nav- and catalog-driven (see AppShell.svelte). This
  // replaces the flat single-page X402.svelte tab bar that used to serve
  // x402.pxke.me too for a few hours on 2026-09-07 before this shipped --
  // that page still serves algorand.pxke.me/x402 unchanged, above.
  type MarketplaceView =
    | { name: 'mktOverview' }
    | { name: 'mktDirectory' }
    | { name: 'mktListing'; url: string }
    | { name: 'mktBoard' }
    | { name: 'mktRequests' }
    | { name: 'mktTrust' }
    | { name: 'mktServices' }
    | { name: 'mktDevelopers' }
    | { name: 'mktRegister' }
    | { name: 'notfound' }

  type MarketplaceLazyName =
    | 'mktDirectory'
    | 'mktListing'
    | 'mktBoard'
    | 'mktRequests'
    | 'mktTrust'
    | 'mktServices'
    | 'mktDevelopers'
    | 'mktRegister'

  const marketplaceLoaders: Record<MarketplaceLazyName, () => Promise<{ default: Component<any> }>> = {
    mktDirectory: () => import('./routes/marketplace/Directory.svelte'),
    mktListing: () => import('./routes/marketplace/ListingDetail.svelte'),
    mktBoard: () => import('./routes/marketplace/Board.svelte'),
    mktRequests: () => import('./routes/marketplace/Requests.svelte'),
    mktTrust: () => import('./routes/marketplace/Trust.svelte'),
    mktServices: () => import('./routes/marketplace/Services.svelte'),
    mktDevelopers: () => import('./routes/marketplace/Developers.svelte'),
    mktRegister: () => import('./routes/marketplace/Register.svelte'),
  }

  let mktLazy = $state<Partial<Record<MarketplaceLazyName, Component<any>>>>({})

  // Paths tonight's brief live deploy of x402.pxke.me actually served (the
  // old X402.svelte tab routing, /x402 prefix included, per the algorand.
  // pxke.me/x402 → x402.pxke.me$request_uri 301 in
  // deploy/nginx/algorand-platform.conf) -- kept as client-side redirects so
  // the few hours this was live, and any link already shared from it, don't
  // dead-end.
  const MARKETPLACE_LEGACY_REDIRECTS: Record<string, string> = {
    '/x402': '/directory',
    '/x402/board': '/board',
    '/x402/requests': '/requests',
    '/x402/grades': '/trust',
    '/x402/endpoints': '/services',
    '/x402/register': '/list',
  }

  function resolveMarketplaceView(path: string, query: URLSearchParams): MarketplaceView {
    const legacy = MARKETPLACE_LEGACY_REDIRECTS[path]
    if (legacy) {
      queueMicrotask(() => navigate(legacy, true))
      return { name: 'mktOverview' }
    }
    if (path === '/') return { name: 'mktOverview' }
    if (path === '/directory') return { name: 'mktDirectory' }
    if (path === '/listing') {
      const url = query.get('url') ?? ''
      if (!url) {
        queueMicrotask(() => navigate('/directory', true))
        return { name: 'mktDirectory' }
      }
      return { name: 'mktListing', url }
    }
    if (path === '/board') return { name: 'mktBoard' }
    if (path === '/requests') return { name: 'mktRequests' }
    if (path === '/trust') return { name: 'mktTrust' }
    if (path === '/services') return { name: 'mktServices' }
    if (path === '/developers') return { name: 'mktDevelopers' }
    if (path === '/list') return { name: 'mktRegister' }
    return { name: 'notfound' }
  }

  const mktView = $derived.by((): MarketplaceView =>
    config.product === 'marketplace'
      ? resolveMarketplaceView($route.path, $route.query)
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
      {#if mktView.name === 'mktListing'}
        {#key mktView.url}
          {@const C = mktLazy.mktListing!}
          <C url={mktView.url} />
        {/key}
      {:else}
        {@const C = mktLazy[mktView.name]!}
        <C />
      {/if}
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
    {:else if view.name === 'shared'}
      {#key view.token}
        {@const C = lazy.shared!}
        <C token={view.token} />
      {/key}
    {:else if view.name === 'x402'}
      {@const C = lazy.x402!}
      <C tab={view.tab} />
    {:else if view.name === 'x402Endpoints'}
      {@const C = lazy.x402Endpoints!}
      <C />
    {:else}
      {@const C = lazy[view.name]!}
      <C />
    {/if}
  {:else}
    <div class="page"><p class="muted">Loading…</p></div>
  {/if}
</AppShell>
