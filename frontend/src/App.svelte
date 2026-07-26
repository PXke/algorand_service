<script lang="ts">
  import AppShell from './components/AppShell.svelte'
  import { route, matchPath, navigate } from './lib/router'
  import { config } from './lib/config'
  import Home from './routes/Home.svelte'
  import News from './routes/News.svelte'
  import Article from './routes/Article.svelte'
  import Hot from './routes/Hot.svelte'
  import Topics from './routes/Topics.svelte'
  import Topic from './routes/Topic.svelte'
  import Search from './routes/Search.svelte'
  import About from './routes/About.svelte'
  import Contact from './routes/Contact.svelte'
  import Suggestions from './routes/Suggestions.svelte'
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
    | { name: 'search' }
    | { name: 'about' }
    | { name: 'contact' }
    | { name: 'suggestions' }
    | { name: 'admin' }
    | { name: 'notfound' }

  const view = $derived.by((): View => {
    const path = $route.path
    if (path === '/') return { name: 'home' }
    if (path === '/news') return { name: 'news' }
    const article = matchPath('/news/articles/:articleId', path)
    if (article) return { name: 'article', id: article.articleId }
    if (path === '/hot') return { name: 'hot', rank: 'hot' }
    if (path === '/top') return { name: 'hot', rank: 'top' }
    if (path === '/topics') return { name: 'topics' }
    const topic = matchPath('/topic/:tag', path)
    if (topic) return { name: 'topic', tag: topic.tag }
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

  let AdminHub = $state<Component | null>(null)

  $effect(() => {
    if (view.name === 'admin' && !AdminHub) {
      void import('./routes/admin/AdminHub.svelte').then((m) => {
        AdminHub = m.default
      })
    }
  })
</script>

<AppShell>
  {#if view.name === 'home'}
    <Home />
  {:else if view.name === 'news'}
    <News />
  {:else if view.name === 'article'}
    {#key view.id}
      <Article articleId={view.id} />
    {/key}
  {:else if view.name === 'hot'}
    {#key view.rank}
      <Hot rank={view.rank} />
    {/key}
  {:else if view.name === 'topics'}
    <Topics />
  {:else if view.name === 'topic'}
    {#key view.tag}
      <Topic tag={view.tag} />
    {/key}
  {:else if view.name === 'search'}
    <Search />
  {:else if view.name === 'about'}
    <About />
  {:else if view.name === 'contact'}
    <Contact />
  {:else if view.name === 'suggestions'}
    <Suggestions />
  {:else if view.name === 'admin'}
    {#if AdminHub}
      <AdminHub />
    {:else}
      <div class="page"><p class="muted">Loading admin…</p></div>
    {/if}
  {:else}
    <NotFound />
  {/if}
</AppShell>
