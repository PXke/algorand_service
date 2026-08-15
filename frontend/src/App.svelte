<script lang="ts">
  import AppShell from './components/AppShell.svelte'
  import { route, matchPath, navigate } from './lib/router'
  import { splitLocalePath } from './lib/paths'
  import { config } from './lib/config'
  import Home from './routes/Home.svelte'
  import NotFound from './routes/NotFound.svelte'
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
    search: () => import('./routes/Search.svelte'),
    about: () => import('./routes/About.svelte'),
    contact: () => import('./routes/Contact.svelte'),
    suggestions: () => import('./routes/Suggestions.svelte'),
    admin: () => import('./routes/admin/AdminHub.svelte'),
    shared: () => import('./routes/SharedArticle.svelte'),
  }

  let lazy = $state<Partial<Record<LazyName, Component<any>>>>({})

  const view = $derived.by((): View => {
    const path = $route.path
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
    if (path === '/admin' || path === '/sources') {
      if (path === '/sources') queueMicrotask(() => navigate('/admin', true))
      return { name: 'admin' }
    }
    return { name: 'notfound' }
  })

  async function reloadOnceForStaleChunk(): Promise<void> {
    // After a deploy, an open tab (or SW precache) may still hold an old
    // entrypoint that points at deleted hashed chunks. Drop the SW + caches
    // so the reload fetches a fresh index instead of looping on the stale one.
    try {
      const key = 'pxke-chunk-reload'
      if (sessionStorage.getItem(key)) return
      sessionStorage.setItem(key, '1')
    } catch {
      /* ignore */
    }
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations()
        await Promise.all(regs.map((r) => r.unregister()))
      }
      if ('caches' in window) {
        const keys = await caches.keys()
        await Promise.all(keys.map((k) => caches.delete(k)))
      }
    } catch {
      /* ignore */
    }
    window.location.reload()
  }

  $effect(() => {
    const name = view.name
    if (name === 'home' || name === 'notfound') return
    const key = name as LazyName
    if (lazy[key] || !loaders[key]) return
    void loaders[key]()
      .then((m) => {
        lazy = { ...lazy, [key]: m.default }
        try {
          sessionStorage.removeItem('pxke-chunk-reload')
        } catch {
          /* ignore */
        }
      })
      .catch(() => {
        void reloadOnceForStaleChunk()
      })
  })
</script>

<AppShell>
  {#if view.name === 'home'}
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
