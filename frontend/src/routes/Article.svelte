<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale, localeTag } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { explorerTxUrl as txUrl, isAlgorandTxid } from '../lib/config'
  import { readingMinutes } from '../lib/reading'
  import {
    clearContinue,
    readContinue,
    rememberContinue,
    saveArticleScroll,
    takeArticleScroll,
  } from '../lib/continueReading'
  import { withLang, articleHref } from '../lib/paths'
  import { articleChromeCollapsed } from '../lib/articleChrome'
  import { displayTagLabel, orderReaderTags, primaryTopic, topicColor } from '../lib/tags'
  import Markdown from '../components/Markdown.svelte'
  import StoryRow from '../components/StoryRow.svelte'
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

  $effect(() => {
    const id = articleId
    const lang = $activeLocale
    let cancelled = false
    loading = true
    error = null
    removed = false
    newer = null
    older = null
    void (async () => {
      try {
        const next = await newsApi.fetchArticle(id, lang)
        if (cancelled) return
        article = next
        rememberContinue({
          articleId: id,
          title: next.title?.trim() || t($messages, 'pageTitleArticle'),
          path: withLang(articleCanonicalPath(id, lang), lang),
        })
        const topic = primaryTopic(next.tags)
        if (topic) {
          const feed = await newsApi.fetchFeedPage({
            limit: 24,
            tag: topic,
            lang,
          })
          if (cancelled) return
          const items = feed.items
          const idx = items.findIndex((a) => a.article_id === id)
          newer = idx > 0 ? items[idx - 1] : null
          older = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null
          related = items
            .filter((a) => a.article_id !== id)
            .slice(0, 4)
        } else {
          related = []
        }
        const y = takeArticleScroll(id)
        if (y != null) {
          requestAnimationFrame(() => window.scrollTo(0, y))
        }
      } catch (e) {
        if (cancelled) return
        article = null
        related = []
        if (e instanceof ApiException && (e.statusCode === 404 || e.statusCode === 410)) {
          removed = true
          error = null
        } else {
          error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
        }
      } finally {
        if (!cancelled) loading = false
      }
    })()
    return () => {
      cancelled = true
      saveArticleScroll(id, window.scrollY)
    }
  })

  $effect(() => {
    const id = articleId
    const onScroll = () => {
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
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
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
  const tone = $derived(topicColor(topic))
  const byline = $derived.by(() => {
    if (!article || !article.published_at_epoch) return ''
    return new Date(article.published_at_epoch * 1000).toLocaleDateString(
      localeTag($activeLocale),
      {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      },
    )
  })
  const mins = $derived(readingMinutes(article?.body))

  const headline = $derived(article?.title?.trim() || t($messages, 'pageTitleArticle'))
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
      <!-- A running head, the way a printed interior page carries the paper's
           name: while reading, the masthead is hidden, and without this there
           was no link back to the front page anywhere on screen. -->
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
        <BrandMark size={24} />
      </a>
      <strong class="sticky-title">{headline}</strong>
      <ShareBar url={canonicalPath} title={headline} compact />
    </div>
  </div>
{/if}

<div class="page">
  <a
    href="/news"
    class="back"
    onclick={(e) => {
      e.preventDefault()
      navigate('/news')
    }}>{t($messages, 'backToFeed')}</a
  >

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
    <article class="reading" bind:this={readingEl} style="--tone:{tone}">
      <span class="accent-slug"></span>
      <p class="kicker">{kicker}</p>
      <h1 bind:this={titleEl}>{article.title}</h1>
      <p class="lede-meta muted">
        <span>{byline}</span>
        <span aria-hidden="true">·</span>
        <span>{t($messages, 'articleReadingTime', { count: mins })}</span>
      </p>

      <Markdown source={article.body ?? ''} />

      <footer class="end-matter">
        <ShareBar url={canonicalPath} title={headline} />
        {#if displayTags.length}
          <div class="tags">
            {#each displayTags as tag}
              <a
                href={`/topic/${encodeURIComponent(tag)}`}
                onclick={(e) => {
                  e.preventDefault()
                  navigate(`/topic/${encodeURIComponent(tag)}`)
                }}>#{displayTagLabel(tag)}</a
              >
            {/each}
          </div>
        {/if}
        <div class="end-meta muted">
          {#if (article.views ?? 0) > 0}
            <span>{tPlural($messages, 'readsCount', article.views ?? 0)}</span>
          {/if}
          {#if article.source_url}
            {#if (article.views ?? 0) > 0}<span aria-hidden="true">·</span>{/if}
            <a href={article.source_url} target="_blank" rel="noopener"
              >{t($messages, 'articleViewSource')}</a
            >
          {/if}
          {#if isAlgorandTxid(article.trigger_txid)}
            <span aria-hidden="true">·</span>
            <a href={txUrl(article.trigger_txid!)} target="_blank" rel="noopener">Tx</a>
          {/if}
        </div>
        <aside class="provenance muted">
          <strong>{t($messages, 'aboutAiHeading')}</strong>
          {t($messages, 'articleProvenanceNote')}
        </aside>
      </footer>
    </article>

    {#if newer || older}
      <nav class="topic-nav" aria-label={t($messages, 'articleTopicNav')}>
        {#if older}
          <a
            class="nav-card older"
            href={articleHref(older.article_id, null, older.slug)}
            onclick={(e) => {
              e.preventDefault()
              goArticle(older!)
            }}
          >
            <span class="nav-label">{t($messages, 'articleOlder')}</span>
            <strong>{older.title}</strong>
          </a>
        {/if}
        {#if newer}
          <a
            class="nav-card newer"
            href={articleHref(newer.article_id, null, newer.slug)}
            onclick={(e) => {
              e.preventDefault()
              goArticle(newer!)
            }}
          >
            <span class="nav-label">{t($messages, 'articleNewer')}</span>
            <strong>{newer.title}</strong>
          </a>
        {/if}
      </nav>
    {/if}

    {#if related.length}
      <section class="related">
        <SectionRule label={t($messages, 'articleRelatedTitle')} />
        <div class="related-grid">
          {#each related as item}
            <StoryRow article={item} dense />
          {/each}
        </div>
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
    padding: 0 12px;
    padding-top: calc(3px + env(safe-area-inset-top, 0px));
    transform: translateY(-110%);
    opacity: 0;
    pointer-events: none;
    /* Opaque, like the masthead — prose scrolling under a translucent strip
       reads as a smear, and the blur cost a full-page composite per frame. */
    background: var(--app-bar);
    border-top: 3px solid var(--accent);
    border-bottom: 1px solid var(--border);
    transition:
      transform 0.24s cubic-bezier(0.22, 1, 0.36, 1),
      opacity 0.18s ease;
  }
  @media (min-width: 860px) {
    .sticky-bar {
      padding: 0 20px;
      padding-top: calc(3px + env(safe-area-inset-top, 0px));
    }
  }
  .sticky-bar.on {
    transform: translateY(0);
    opacity: 1;
    pointer-events: auto;
  }
  .sticky-inner {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 44px;
    max-width: var(--max-reading);
    margin: 0 auto;
  }
  .sticky-home {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    line-height: 0;
    border-radius: 2px;
  }
  .sticky-title {
    flex: 1;
    min-width: 0;
    font-family: var(--font-display);
    font-size: 14px;
    font-weight: 700;
    line-height: 1.25;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .back {
    display: none;
    margin-bottom: 18px;
    font-size: 0.9rem;
    font-weight: 600;
    transition: color 0.2s ease;
  }
  @media (min-width: 860px) {
    .back {
      display: inline-block;
    }
  }
  .reading {
    max-width: var(--max-reading);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .kicker {
    margin: 0;
  }
  h1 {
    margin: 0;
    font-size: 28px;
    line-height: 1.12;
    letter-spacing: -0.5px;
  }
  @media (min-width: 520px) {
    h1 {
      font-size: 38px;
    }
  }
  .lede-meta {
    margin: 0 0 6px;
    font-size: 0.88rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.45rem;
    align-items: baseline;
  }
  /* The tail used to be six modules at identical weight, so the article
     dissolved rather than ended. Now: a firm rule, then share/tags/meta as
     one group, then the provenance note visibly set apart. */
  .end-matter {
    display: flex;
    flex-direction: column;
    gap: 16px;
    margin-top: 40px;
    padding-top: 24px;
    border-top: 2px solid var(--on-surface);
  }
  .end-matter :global(.share .share-btn) {
    width: 100%;
    justify-content: center;
  }
  @media (min-width: 520px) {
    .end-matter :global(.share .share-btn) {
      width: auto;
    }
  }
  .end-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.45rem;
    align-items: baseline;
    font-size: 0.9rem;
  }
  .end-meta a {
    font-weight: 600;
  }
  .provenance {
    margin: 6px 0 0;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--callout);
    font-size: 0.86rem;
    line-height: 1.5;
  }
  .provenance strong {
    display: block;
    margin-bottom: 4px;
    color: var(--on-surface);
    font-size: 11px;
    letter-spacing: 0.6px;
    text-transform: uppercase;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .tags a {
    background: color-mix(in srgb, var(--tone, var(--accent)) 10%, transparent);
    padding: 8px 12px;
    min-height: 36px;
    display: inline-flex;
    align-items: center;
    border-radius: 8px;
    font-size: 0.84rem;
    font-weight: 600;
    color: var(--tone, var(--primary));
    text-decoration: none;
    transition: background 0.2s ease, color 0.2s ease;
  }
  .tags a:hover {
    background: color-mix(in srgb, var(--tone, var(--accent)) 20%, transparent);
    text-decoration: none;
  }
  .topic-nav {
    display: grid;
    gap: 12px;
    margin-top: 32px;
    max-width: var(--max-reading);
  }
  @media (min-width: 640px) {
    .topic-nav {
      grid-template-columns: 1fr 1fr;
    }
  }
  .nav-card {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 14px 0;
    border-top: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
    min-width: 0;
  }
  .nav-card:hover {
    text-decoration: none;
  }
  .nav-card:hover strong {
    color: var(--primary);
  }
  .nav-card.newer {
    text-align: end;
  }
  @media (max-width: 639px) {
    .nav-card.newer {
      text-align: start;
    }
  }
  .nav-label {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .nav-card strong {
    font-family: var(--font-display);
    font-size: 15px;
    line-height: 1.3;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    transition: color 0.2s ease;
  }
  .related {
    margin-top: 36px;
  }
  .related-grid {
    display: grid;
    gap: 0;
  }
  @media (min-width: 700px) {
    .related-grid {
      grid-template-columns: 1fr 1fr;
      column-gap: 28px;
    }
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
  /* `.lede` is tagged in Markdown.svelte — the opening paragraph can follow
     a lead image and/or a heading, which no CSS selector can pin down. */
  .reading :global(.md > p.lede::first-letter) {
    float: inline-start;
    font-family: var(--font-serif);
    font-size: 3.4em;
    font-weight: 700;
    line-height: 0.85;
    padding-inline-end: 10px;
    padding-block-end: 4px;
    color: var(--tone, var(--primary));
  }
  @media (max-width: 519px) {
    .reading :global(.md > p.lede::first-letter) {
      font-size: 2.6em;
      padding-inline-end: 8px;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .progress span,
    .sticky-bar {
      transition: none;
    }
  }
  @media print {
    .back,
    .related,
    .topic-nav,
    .tags,
    .progress,
    .sticky-bar,
    .byline :global(.share),
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
    .reading :global(.md > p:first-of-type::first-letter) {
      color: #000;
    }
  }
</style>
