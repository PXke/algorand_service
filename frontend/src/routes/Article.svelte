<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { explorerTxUrl as txUrl, isAlgorandTxid } from '../lib/config'
  import { readingMinutes } from '../lib/reading'
  import { formatDispatchStamp } from '../lib/liveClock'
  import {
    clearContinue,
    readContinue,
    rememberContinue,
    saveArticleScroll,
    takeArticleScroll,
  } from '../lib/continueReading'
  import { withLang, articleHref } from '../lib/paths'
  import { articleChromeCollapsed } from '../lib/articleChrome'
  import {
    displayTagLabel,
    orderReaderTags,
    primaryTopic,
    topicColor,
    readerDesk,
    deskMessageKey,
  } from '../lib/tags'
  import { articleImageUrl, looksLikeFaviconUrl } from '../lib/images'
  import Markdown from '../components/Markdown.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import ShareBar from '../components/ShareBar.svelte'
  import BrandMark from '../components/BrandMark.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
  import { ApiException } from '../lib/api/client'
  import {
    SITE_TAGLINE,
    articleCanonicalPath,
    articleOgImageUrl,
    newsArticleJsonLd,
    ogLocaleFor,
    truncateMeta,
  } from '../lib/seo'
  import { staggerMs } from '../lib/motion'

  let { articleId }: { articleId: string } = $props()

  let article = $state<ArticleItem | null>(null)
  let related = $state<ArticleItem[]>([])
  let newer = $state<ArticleItem | null>(null)
  let older = $state<ArticleItem | null>(null)
  let error = $state<string | null>(null)
  let removed = $state(false)
  let loading = $state(true)
  let progress = $state(0)
  let stickyOn = $state(false)
  let titleEl: HTMLHeadingElement | undefined = $state()
  let readingEl: HTMLElement | undefined = $state()
  let heroFailed = $state<string | null>(null)

  $effect(() => {
    const id = articleId
    const lang = $activeLocale
    const ac = new AbortController()
    loading = true
    error = null
    removed = false
    newer = null
    older = null
    related = []
    heroFailed = null
    void (async () => {
      try {
        const next = await newsApi.fetchArticle(id, lang, ac.signal)
        if (ac.signal.aborted) return
        article = next
        loading = false
        rememberContinue({
          articleId: id,
          title: next.title?.trim() || t($messages, 'pageTitleArticle'),
          path: withLang(articleCanonicalPath(id, lang), lang),
        })
        const y = takeArticleScroll(id)
        if (y != null) {
          requestAnimationFrame(() => window.scrollTo(0, y))
        }
        const topic = primaryTopic(next.tags)
        if (!topic) return
        const feed = await newsApi.fetchFeedPage({
          limit: 24,
          tag: topic,
          lang,
          signal: ac.signal,
        })
        if (ac.signal.aborted) return
        const items = feed.items
        // `id` is the route param, which is the article's SLUG, not its UUID
        // -- fetchArticle() resolves either, but `next.article_id` is always
        // the real UUID the feed items are keyed by. Comparing feed items
        // against the raw slug never matched, so this article could appear
        // in its own "related stories" and prev/next navigation was always
        // null (found 2026-08-09, reported live: self-listed as related).
        const selfId = next.article_id
        const idx = items.findIndex((a) => a.article_id === selfId)
        newer = idx > 0 ? items[idx - 1] : null
        older = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null
        related = items.filter((a) => a.article_id !== selfId).slice(0, 4)
      } catch (e) {
        if (ac.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        article = null
        related = []
        loading = false
        if (e instanceof ApiException && (e.statusCode === 404 || e.statusCode === 410)) {
          removed = true
          error = null
        } else {
          error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
        }
      }
    })()
    return () => {
      ac.abort()
      saveArticleScroll(id, window.scrollY)
    }
  })

  $effect(() => {
    const id = articleId
    let raf = 0
    const update = () => {
      raf = 0
      saveArticleScroll(id, window.scrollY)
      const el = readingEl
      if (!el) {
        progress = 0
        return
      }
      const top = el.getBoundingClientRect().top + window.scrollY
      const span = Math.max(1, el.offsetHeight - window.innerHeight * 0.55)
      progress = Math.min(1, Math.max(0, (window.scrollY - top + 48) / span))
      /* Finished it — so stop offering to resume it. "Continue reading" kept
         pointing at articles the reader had already read to the end, because
         nothing ever cleared the entry except opening a different story. */
      if (progress >= 0.99) {
        const saved = readContinue()
        if (saved?.articleId === id) clearContinue()
        saveArticleScroll(id, 0)
      }
    }
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(update)
    }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
      if (raf) cancelAnimationFrame(raf)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  })

  $effect(() => {
    const node = titleEl
    if (!node || typeof IntersectionObserver === 'undefined') {
      stickyOn = false
      articleChromeCollapsed.set(false)
      return
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        const on = !entry.isIntersecting && entry.boundingClientRect.top < 0
        stickyOn = on
        articleChromeCollapsed.set(on)
      },
      { rootMargin: '-56px 0px 0px 0px', threshold: 0 },
    )
    io.observe(node)
    return () => {
      io.disconnect()
      articleChromeCollapsed.set(false)
    }
  })

  $effect(() => {
    return () => articleChromeCollapsed.set(false)
  })

  const displayTags = $derived(orderReaderTags(article?.tags))
  const topic = $derived(article ? primaryTopic(article.tags) : null)
  const kicker = $derived(topic ? displayTagLabel(topic) : t($messages, 'pageTitleArticle'))
  const deskId = $derived(article ? readerDesk(article.tags) : 'wire')
  const deskLabel = $derived(t($messages, deskMessageKey(deskId)))
  const isSpecialEdition = $derived(
    (article?.tags ?? []).some((tag) => String(tag).toLowerCase() === 'special-edition'),
  )
  const tone = $derived(topicColor(topic))
  const stamp = $derived.by(() => {
    if (!article?.published_at_epoch) return ''
    return formatDispatchStamp(article.published_at_epoch, $activeLocale)
  })
  const mins = $derived(readingMinutes(article?.body))
  const views = $derived(article?.views ?? 0)

  function deskOf(item: ArticleItem): string {
    const desk = primaryTopic(item.tags)
    return desk ? displayTagLabel(desk) : t($messages, 'pageTitleArticle')
  }

  const headline = $derived(article?.title?.trim() || t($messages, 'pageTitleArticle'))
  const heroRaw = $derived.by(() => {
    const url = article?.image_url?.trim()
    if (!url || looksLikeFaviconUrl(url)) return ''
    return url
  })
  const heroSrc = $derived(heroRaw ? articleImageUrl({ image_url: heroRaw }) : null)
  const showHero = $derived(Boolean(heroSrc) && heroSrc !== heroFailed)
  const description = $derived(
    article
      ? truncateMeta(article.summary || article.body || SITE_TAGLINE)
      : SITE_TAGLINE,
  )
  const canonicalPath = $derived(articleCanonicalPath(articleId, $activeLocale))
  const jsonLd = $derived(
    article
      ? newsArticleJsonLd({
          articleId,
          title: headline,
          description,
          publishedEpoch: article.published_at_epoch,
          tags: article.tags,
          lang: $activeLocale,
        })
      : null,
  )

  function goArticle(item: ArticleItem) {
    navigate(articleHref(item.article_id, null, item.slug))
  }
</script>

{#if article && !loading}
  <PageMeta
    title={headline}
    {description}
    path={canonicalPath}
    ogType="article"
    image={articleOgImageUrl(articleId)}
    imageAlt={headline}
    ogLocale={ogLocaleFor($activeLocale)}
    {jsonLd}
  />
{:else if removed}
  <PageMeta title={t($messages, 'articleRemovedTitle')} description={t($messages, 'articleRemovedBody')} />
{:else if error}
  <PageMeta title={t($messages, 'pageTitleArticle')} description={SITE_TAGLINE} />
{:else}
  <PageMeta title={t($messages, 'pageTitleArticle')} description={SITE_TAGLINE} path={canonicalPath} />
{/if}

{#if article && !loading}
  <div
    class="progress"
    role="progressbar"
    aria-valuemin={0}
    aria-valuemax={100}
    aria-valuenow={Math.round(progress * 100)}
    aria-label={t($messages, 'articleReadingProgress')}
  >
    <span style="transform: scaleX({progress})"></span>
  </div>

  <div class="sticky-bar" class:on={stickyOn} aria-hidden={!stickyOn}>
    <div class="sticky-inner">
      <!-- Running head: desk + slug, not an app toolbar. The masthead is
           hidden mid-read, so this strip is the paper's name on the interior. -->
      <a
        class="sticky-back"
        href="/news"
        aria-label={t($messages, 'backToFeed')}
        title={t($messages, 'backToFeed')}
        tabindex={stickyOn ? 0 : -1}
        onclick={(e) => {
          e.preventDefault()
          navigate('/news')
        }}
      >
        <span class="chevron" aria-hidden="true"></span>
        {t($messages, 'navLatest')}
      </a>
      <a
        class="sticky-home"
        href="/"
        aria-label={t($messages, 'appTitle')}
        tabindex={stickyOn ? 0 : -1}
        onclick={(e) => {
          e.preventDefault()
          navigate('/')
        }}
      >
        <BrandMark size={22} />
      </a>
      <p class="sticky-desk kicker">{deskLabel}</p>
      <strong class="sticky-title">{headline}</strong>
      <ShareBar url={canonicalPath} title={headline} compact />
    </div>
  </div>
{/if}

<div class="page page-reading">
  {#if loading}
    <FeedSkeleton rows={4} />
  {:else if removed}
    <div class="gone">
      <span class="accent-slug"></span>
      <h1>{t($messages, 'articleRemovedTitle')}</h1>
      <p class="muted">{t($messages, 'articleRemovedBody')}</p>
      <div class="gone-actions">
        <button class="btn btn-primary" type="button" onclick={() => navigate('/news')}>
          {t($messages, 'backToFeed')}
        </button>
        <button class="btn" type="button" onclick={() => navigate('/')}>
          {t($messages, 'navHome')}
        </button>
      </div>
    </div>
  {:else if error}
    <p class="err">{error}</p>
  {:else if article}
    {#key article.article_id}
    <article class="reading article-in" bind:this={readingEl} style="--tone:{tone}">
      <span class="accent-slug"></span>
      <div class="kicker-row">
        {#if topic}
          <a
            class="kicker"
            href={`/topic/${encodeURIComponent(topic)}`}
            onclick={(e) => {
              e.preventDefault()
              navigate(`/topic/${encodeURIComponent(topic)}`)
            }}>{kicker}</a
          >
        {:else}
          <p class="kicker">{kicker}</p>
        {/if}
        {#if isSpecialEdition}
          <span class="special-edition-badge">{t($messages, 'specialEditionBadge')}</span>
        {/if}
      </div>
      <h1 bind:this={titleEl}>{article.title}</h1>
      <p class="folio wire-stamp">
        <span>{deskLabel}</span>
        {#if stamp}
          <span class="sep" aria-hidden="true">·</span>
          <span>{stamp}</span>
        {/if}
        <span class="sep" aria-hidden="true">·</span>
        <span>{t($messages, 'articleReadingTime', { count: mins })}</span>
        {#if views > 0}
          <span class="sep" aria-hidden="true">·</span>
          <span>{tPlural($messages, 'readsCount', views)}</span>
        {/if}
      </p>

      {#if showHero}
        <figure class="hero-plate">
          <img
            src={heroSrc}
            alt=""
            decoding="async"
            onerror={() => {
              heroFailed = heroSrc
            }}
          />
        </figure>
      {/if}

      <Markdown source={article.body ?? ''} skipHref={heroRaw} />

      <footer class="end-matter">
        <aside class="provenance">
          <p class="kicker">{t($messages, 'aboutAiHeading')}</p>
          <p class="note">{t($messages, 'articleProvenanceNote')}</p>
          {#if article.source_url || isAlgorandTxid(article.trigger_txid)}
            <p class="cite">
              {#if article.source_url}
                <a href={article.source_url} target="_blank" rel="noopener"
                  >{t($messages, 'articleViewSource')}</a
                >
              {/if}
              {#if isAlgorandTxid(article.trigger_txid)}
                {#if article.source_url}<span class="sep" aria-hidden="true">·</span>{/if}
                <a href={txUrl(article.trigger_txid!)} target="_blank" rel="noopener">Tx</a>
              {/if}
            </p>
          {/if}
        </aside>
        {#if displayTags.length}
          <div class="tags">
            {#each displayTags as tag (tag)}
              <a
                href={`/topic/${encodeURIComponent(tag)}`}
                onclick={(e) => {
                  e.preventDefault()
                  navigate(`/topic/${encodeURIComponent(tag)}`)
                }}>{displayTagLabel(tag)}</a
              >
            {/each}
          </div>
        {/if}
        <ShareBar url={canonicalPath} title={headline} compact />
      </footer>
    </article>
    {/key}

    {#if newer || older}
      <nav class="topic-nav enter" aria-label={t($messages, 'articleTopicNav')}>
        {#if older}
          <a
            class="older"
            href={articleHref(older.article_id, null, older.slug)}
            onclick={(e) => {
              e.preventDefault()
              goArticle(older!)
            }}
          >
            <span class="nav-dir">{t($messages, 'articleOlder')}</span>
            <strong>{older.title}</strong>
          </a>
        {:else}
          <span></span>
        {/if}
        {#if newer}
          <a
            class="newer"
            href={articleHref(newer.article_id, null, newer.slug)}
            onclick={(e) => {
              e.preventDefault()
              goArticle(newer!)
            }}
          >
            <span class="nav-dir">{t($messages, 'articleNewer')}</span>
            <strong>{newer.title}</strong>
          </a>
        {/if}
      </nav>
    {/if}

    {#if related.length}
      <section class="related enter">
        <SectionRule label={t($messages, 'articleOnDesk')} />
        <ol class="desk-index">
          {#each related as item, i (item.article_id)}
            <li class="enter" style="--enter-delay: {staggerMs(i)}ms">
              <a
                href={articleHref(item.article_id, null, item.slug)}
                onclick={(e) => {
                  e.preventDefault()
                  goArticle(item)
                }}
              >
                <span class="kicker">{deskOf(item)}</span>
                <strong>{item.title}</strong>
              </a>
            </li>
          {/each}
        </ol>
      </section>
    {/if}
  {/if}
</div>

<style>
  .progress {
    position: fixed;
    top: 0;
    inset-inline: 0;
    z-index: 60;
    height: 2.5px;
    padding-top: env(safe-area-inset-top, 0);
    pointer-events: none;
    background: transparent;
    box-sizing: content-box;
  }
  .progress span {
    display: block;
    height: 2.5px;
    width: 100%;
    transform-origin: left center;
    background: var(--accent);
    transition: transform 0.08s linear;
  }
  :global([dir='rtl']) .progress span {
    transform-origin: right center;
  }
  .sticky-bar {
    position: fixed;
    top: 0;
    inset-inline: 0;
    z-index: 45;
    padding-top: env(safe-area-inset-top, 0px);
    transform: translateY(-110%);
    opacity: 0;
    pointer-events: none;
    background: var(--app-bar);
    color: var(--masthead-ink);
    border-bottom: 1px solid var(--border);
    box-shadow: none;
    transition:
      transform 0.24s cubic-bezier(0.22, 1, 0.36, 1),
      opacity 0.18s ease;
  }
  .sticky-bar.on {
    transform: translateY(0);
    opacity: 1;
    pointer-events: auto;
  }
  .sticky-inner {
    display: flex;
    align-items: center;
    gap: 12px;
    height: 44px;
    width: 100%;
    max-width: var(--max-wide);
    margin: 0 auto;
    padding-inline: var(--shell-gutter);
    box-sizing: border-box;
  }
  .sticky-back {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--masthead-muted);
    text-decoration: none;
    min-height: 32px;
  }
  .sticky-back:hover {
    color: var(--accent);
    text-decoration: none;
  }
  .sticky-back .chevron::before {
    content: '‹';
    font-size: 16px;
    line-height: 1;
    font-weight: 400;
  }
  :global([dir='rtl']) .sticky-back .chevron::before {
    content: '›';
  }
  .sticky-home {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    line-height: 0;
  }
  :global(html[data-theme='dark']) .sticky-home :global(.mark) {
    background: transparent;
    color: var(--masthead-ink);
    box-shadow: none;
  }
  .sticky-desk {
    flex-shrink: 0;
    max-width: 14ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .sticky-title {
    flex: 1;
    min-width: 0;
    font-family: var(--font-display);
    font-size: 14px;
    font-weight: 700;
    line-height: 1.25;
    letter-spacing: -0.2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  @media (max-width: 519px) {
    .sticky-inner {
      gap: 8px;
    }
    .sticky-back {
      font-size: 0;
      gap: 0;
    }
    .sticky-back .chevron::before {
      font-size: 22px;
    }
    .sticky-desk {
      display: none;
    }
  }
  .sticky-inner :global(.share) {
    flex-shrink: 0;
  }
  .reading {
    max-width: none;
    display: flex;
    flex-direction: column;
    gap: 14px;
    background: transparent;
    box-shadow: none;
    border: 0;
    border-radius: 0;
  }
  @media (prefers-reduced-motion: no-preference) {
    .reading.article-in > .kicker-row,
    .reading.article-in > h1,
    .reading.article-in > .folio,
    .reading.article-in > .hero-plate,
    .reading.article-in > :global(.md),
    .reading.article-in > .end-matter {
      animation: rise-in 0.48s cubic-bezier(0.22, 1, 0.36, 1) both;
    }
    .reading.article-in > .kicker-row {
      animation-delay: 0ms;
    }
    .reading.article-in > h1 {
      animation-delay: 55ms;
    }
    .reading.article-in > .folio {
      animation-delay: 110ms;
    }
    .reading.article-in > .hero-plate {
      animation-delay: 165ms;
    }
    .reading.article-in > :global(.md) {
      animation-delay: 220ms;
    }
    .reading.article-in > .end-matter {
      animation-delay: 300ms;
    }
    .topic-nav.enter a {
      animation: rise-in 0.42s cubic-bezier(0.22, 1, 0.36, 1) both;
    }
    .topic-nav.enter .older {
      animation-delay: 0ms;
    }
    .topic-nav.enter .newer {
      animation-delay: 70ms;
    }
    .related.enter :global(.section-rule) {
      animation: rise-in 0.4s cubic-bezier(0.22, 1, 0.36, 1) both;
    }
  }
  @media (min-width: 640px) {
    .page.page-reading {
      padding-top: 36px;
      padding-bottom: 72px;
    }
  }
  .kicker {
    margin: 0;
  }
  .kicker-row a.kicker {
    text-decoration: none;
  }
  .kicker-row a.kicker:hover {
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .kicker-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .special-edition-badge {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 0;
    background: var(--accent);
    color: var(--surface);
  }
  h1 {
    margin: 0;
    font-size: var(--fs-article);
    line-height: 1.06;
    letter-spacing: -1px;
  }
  .folio {
    margin: 2px 0 4px;
  }
  .hero-plate {
    margin: 20px 0 8px;
  }
  .hero-plate img {
    display: block;
    width: 100%;
    max-height: 420px;
    object-fit: contain;
    object-position: center;
    background: var(--thumb-plate);
    border: 1px solid var(--border);
    padding: 12px;
  }
  @media (max-width: 519px) {
    .hero-plate {
      margin: 16px 0 8px;
    }
    .hero-plate img {
      max-height: 260px;
      padding: 10px;
    }
  }
  .end-matter {
    display: flex;
    flex-direction: column;
    gap: 20px;
    margin-top: 48px;
    padding-top: 28px;
    border-top: 1px solid var(--border);
  }
  .end-matter :global(.share) {
    align-self: flex-start;
  }
  .end-matter :global(.share.compact .menu) {
    inset-inline-start: 0;
    inset-inline-end: auto;
  }
  .provenance .kicker {
    color: var(--accent);
  }
  .provenance .note {
    margin: 0;
    max-width: 60ch;
    font-family: var(--font-serif);
    font-size: 1.02rem;
    line-height: 1.55;
    color: var(--md-ink);
  }
  .cite {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .cite .sep {
    margin-inline: 6px;
    color: var(--subtle);
  }
  .cite a {
    color: var(--accent);
    text-decoration: none;
  }
  .cite a:hover {
    text-decoration: underline;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0;
  }
  .tags a {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--muted);
    text-decoration: none;
    min-height: 32px;
    display: inline-flex;
    align-items: center;
  }
  .tags a:not(:last-child)::after {
    content: '·';
    margin-inline: 8px;
    color: var(--subtle);
    font-weight: 500;
  }
  .tags a:hover {
    color: var(--accent);
    text-decoration: none;
  }
  /* Prev / next as a wire line, not orphaned cards. A single neighbour
     sits on its side of the hairline; two neighbours face each other. */
  .topic-nav {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 28px;
    margin-top: 36px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .topic-nav a {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
    color: inherit;
    text-decoration: none;
  }
  .topic-nav a:hover {
    text-decoration: none;
  }
  .topic-nav .newer {
    text-align: end;
    justify-self: end;
  }
  .nav-dir {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--accent);
  }
  .older .nav-dir::before {
    content: '← ';
  }
  .newer .nav-dir::after {
    content: ' →';
  }
  :global([dir='rtl']) .older .nav-dir::before {
    content: '→ ';
  }
  :global([dir='rtl']) .newer .nav-dir::after {
    content: ' ←';
  }
  .topic-nav strong {
    font-family: var(--font-display);
    font-size: 16px;
    line-height: 1.3;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .topic-nav a:hover strong {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  @media (max-width: 639px) {
    .topic-nav {
      grid-template-columns: 1fr;
      gap: 18px;
    }
    .topic-nav > span:empty {
      display: none;
    }
    .topic-nav .newer {
      text-align: start;
      justify-self: start;
    }
  }
  .related {
    margin-top: 40px;
    padding: 8px 0 12px;
  }
  .desk-index {
    list-style: none;
    margin: 4px 0 0;
    padding: 0;
  }
  .desk-index li {
    border-bottom: 1px solid var(--border);
  }
  @media (prefers-reduced-motion: no-preference) {
    .desk-index li.enter {
      animation: rise-in 0.42s cubic-bezier(0.22, 1, 0.36, 1) both;
      animation-delay: var(--enter-delay, 0ms);
    }
  }
  .desk-index li:first-child {
    border-top: 1px solid var(--border);
  }
  .desk-index a {
    display: flex;
    flex-direction: column;
    gap: 5px;
    padding: 14px 0;
    color: inherit;
    text-decoration: none;
  }
  .desk-index a:hover {
    text-decoration: none;
  }
  .desk-index strong {
    font-family: var(--font-display);
    font-size: 17px;
    font-weight: 700;
    line-height: 1.28;
    letter-spacing: -0.3px;
  }
  .desk-index a:hover strong {
    color: var(--accent);
  }
  .err {
    color: var(--danger);
  }
  .gone {
    max-width: var(--max-reading);
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .gone h1 {
    font-size: clamp(28px, 4vw, 34px);
  }
  .gone-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 8px;
  }
  @media (prefers-reduced-motion: reduce) {
    .progress span,
    .sticky-bar {
      transition: none;
    }
  }
  @media print {
    .related,
    .topic-nav,
    .tags,
    .progress,
    .sticky-bar,
    :global(.markets),
    :global(.section-nav),
    :global(.site-footer),
    :global(.masthead) {
      display: none !important;
    }
    .page {
      max-width: none;
      padding: 0;
    }
  }
</style>
