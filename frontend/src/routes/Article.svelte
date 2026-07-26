<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale, localeTag } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { explorerTxUrl as txUrl } from '../lib/config'
  import { readingMinutes, imageCreditFromSource } from '../lib/reading'
  import {
    rememberContinue,
    saveArticleScroll,
    takeArticleScroll,
  } from '../lib/continueReading'
  import { withLang } from '../lib/paths'
  import { proxiedImageUrl } from '../lib/images'
  import Markdown from '../components/Markdown.svelte'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import ShareBar from '../components/ShareBar.svelte'
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
  let error = $state<string | null>(null)
  let removed = $state(false)
  let loading = $state(true)

  $effect(() => {
    const id = articleId
    const lang = $activeLocale
    let cancelled = false
    loading = true
    error = null
    removed = false
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
        const tags = next.tags ?? []
        if (tags[0]) {
          const feed = await newsApi.fetchFeedPage({
            limit: 8,
            tag: tags[0],
            lang,
          })
          if (cancelled) return
          related = feed.items.filter((a) => a.article_id !== id).slice(0, 4)
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
    const onScroll = () => saveArticleScroll(id, window.scrollY)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  })

  const kicker = $derived((article && article.tags?.[0]) || t($messages, 'pageTitleArticle'))
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
  const imageCredit = $derived(imageCreditFromSource(article?.source_url))
  const leadSrc = $derived.by(() => {
    const u = article?.image_url?.trim()
    return u ? proxiedImageUrl(u) : null
  })

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
    <article class="reading">
      <span class="accent-slug"></span>
      <p class="kicker">{kicker}</p>
      <h1>{article.title}</h1>
      {#if article.summary}
        <p class="deck muted">{article.summary}</p>
      {/if}
      <div class="byline">
        <strong>{byline}</strong>
        <span class="muted meta">
          <span>{t($messages, 'articleReadingTime', { count: mins })}</span>
          {#if (article.views ?? 0) > 0}
            · {tPlural($messages, 'readsCount', article.views ?? 0)}
          {/if}
          {#if article.source_url}
            ·
            <a href={article.source_url} target="_blank" rel="noopener">{t($messages, 'articleViewSource')}</a>
          {/if}
          {#if article.trigger_txid}
            · <a href={txUrl(article.trigger_txid)} target="_blank" rel="noopener">Tx</a>
          {/if}
        </span>
        <ShareBar url={canonicalPath} title={headline} />
      </div>
      {#if leadSrc}
        <figure class="lead-art">
          <img
            src={leadSrc}
            alt=""
            width={680}
            height={425}
            loading="eager"
            decoding="async"
            onerror={(e) => ((e.currentTarget as HTMLImageElement).closest('figure') as HTMLElement).style.display = 'none'}
          />
          {#if imageCredit}
            <figcaption>{t($messages, 'articleImageCredit', { host: imageCredit })}</figcaption>
          {/if}
        </figure>
      {/if}
      <Markdown source={article.body ?? ''} />
      {#if article.tags?.length}
        <div class="tags">
          {#each article.tags as tag}
            <a
              href={`/topic/${encodeURIComponent(tag)}`}
              onclick={(e) => {
                e.preventDefault()
                navigate(`/topic/${encodeURIComponent(tag)}`)
              }}>#{tag}</a
            >
          {/each}
        </div>
      {/if}
    </article>

    {#if related.length}
      <section class="related">
        <SectionRule label={t($messages, 'articleRelatedTitle')} />
        {#each related as item}
          <StoryRow article={item} dense />
        {/each}
      </section>
    {/if}
  {/if}
</div>

<style>
  .back {
    display: inline-block;
    margin-bottom: 18px;
    font-size: 0.9rem;
    font-weight: 600;
    transition: color 0.2s ease;
  }
  .reading {
    max-width: var(--max-reading);
    display: flex;
    flex-direction: column;
    gap: 12px;
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
  .deck {
    margin: 0;
    font-size: 18px;
    line-height: 1.55;
  }
  @media (min-width: 520px) {
    .deck {
      font-size: 20px;
    }
  }
  .byline {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    padding: 14px 0;
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    font-size: 0.92rem;
  }
  .byline :global(.share) {
    margin-inline-start: auto;
  }
  .meta a {
    font-weight: 600;
  }
  .lead-art {
    margin: 4px 0 8px;
  }
  .lead-art img {
    display: block;
    width: 100%;
    max-height: 420px;
    object-fit: contain;
    object-position: center;
    border-radius: 12px;
    background: var(--callout);
  }
  .lead-art figcaption {
    margin-top: 8px;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
  .tags a {
    background: var(--accent-soft);
    padding: 5px 11px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--primary);
    text-decoration: none;
    transition: background 0.2s ease, color 0.2s ease;
  }
  .tags a:hover {
    background: color-mix(in srgb, var(--accent) 18%, var(--accent-soft));
    text-decoration: none;
  }
  .related {
    margin-top: 36px;
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
  .reading :global(.md > p:first-of-type::first-letter) {
    float: inline-start;
    font-family: var(--font-display);
    font-size: 3.4em;
    font-weight: 700;
    line-height: 0.85;
    padding-inline-end: 10px;
    padding-block-end: 4px;
    color: var(--primary);
  }
  @media print {
    .back,
    .related,
    .tags,
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
