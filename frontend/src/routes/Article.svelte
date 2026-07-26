<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { explorerTxUrl as txUrl } from '../lib/config'
  import Markdown from '../components/Markdown.svelte'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
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

  onMount(() => {
    void (async () => {
      try {
        article = await newsApi.fetchArticle(articleId, $activeLocale)
        const tags = article.tags ?? []
        if (tags[0]) {
          const feed = await newsApi.fetchFeedPage({
            limit: 8,
            tag: tags[0],
            lang: $activeLocale,
          })
          related = feed.items.filter((a) => a.article_id !== articleId).slice(0, 4)
        }
      } catch (e) {
        if (e instanceof ApiException && (e.statusCode === 404 || e.statusCode === 410)) {
          removed = true
          error = null
        } else {
          error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
        }
      } finally {
        loading = false
      }
    })()
  })

  const kicker = $derived((article && article.tags?.[0]) || 'Article')
  const byline = $derived.by(() => {
    if (!article || !article.published_at_epoch) return ''
    return new Date(article.published_at_epoch * 1000).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    })
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
    <p class="muted">{t($messages, 'loading')}</p>
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
          {#if (article.views ?? 0) > 0}
            {tPlural($messages, 'readsCount', article.views ?? 0)}
          {/if}
          {#if article.source_url}
            {#if (article.views ?? 0) > 0}·{/if}
            <a href={article.source_url} target="_blank" rel="noopener">Source</a>
          {/if}
          {#if article.trigger_txid}
            · <a href={txUrl(article.trigger_txid)} target="_blank" rel="noopener">Tx</a>
          {/if}
        </span>
      </div>
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
        <SectionRule label="Related" />
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
    align-items: baseline;
    padding: 12px 0;
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    font-size: 0.92rem;
  }
  .meta a {
    font-weight: 600;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
  .tags a {
    background: var(--accent-soft);
    padding: 4px 10px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--primary);
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
</style>
