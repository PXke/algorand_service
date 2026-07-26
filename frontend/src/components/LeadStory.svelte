<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import { articleLogoUrl, proxiedImageUrl } from '../lib/images'
  import { articleHref } from '../lib/paths'
  import { navigate } from '../lib/router'

  let { article }: { article: ArticleItem } = $props()

  const href = $derived(articleHref(article.article_id))
  /** Server already dimension-vets image_url — trust it; favicon only if absent. */
  const media = $derived.by(() => {
    const u = article.image_url?.trim()
    if (u) return { src: proxiedImageUrl(u), logo: false }
    const logo = articleLogoUrl({
      sourceUrl: article.source_url,
      serviceId: article.service_id,
    })
    return logo ? { src: logo, logo: true } : null
  })
  const kicker = $derived(article.tags?.[0] ?? 'Lead')
</script>

<a
  class="lead"
  {href}
  onclick={(e) => {
    e.preventDefault()
    navigate(href)
  }}
>
  <div class="copy">
    <span class="accent-slug"></span>
    <p class="kicker">{kicker}</p>
    <h1 class="title">{article.title ?? 'Untitled'}</h1>
    {#if article.summary}
      <p class="deck muted">{article.summary}</p>
    {/if}
  </div>
  {#if media}
    <div class="media" class:logo={media.logo}>
      <img
        src={media.src}
        alt=""
        width={media.logo ? 160 : 680}
        height={media.logo ? 160 : 425}
        loading="eager"
        fetchpriority="high"
        decoding="async"
        onerror={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')}
      />
    </div>
  {/if}
</a>

<style>
  .lead {
    display: grid;
    gap: 18px;
    color: inherit;
    text-decoration: none;
    padding-bottom: 8px;
  }
  @media (min-width: 640px) {
    .lead {
      grid-template-columns: 1fr minmax(240px, 38%);
      align-items: start;
      gap: 28px;
    }
  }
  .lead:hover {
    text-decoration: none;
  }
  .lead:hover .title {
    color: var(--primary);
  }
  .lead:hover .media img {
    transform: scale(1.035);
    filter: saturate(1);
  }
  .copy {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .title {
    margin: 0;
    font-family: var(--font-display);
    font-weight: 700;
    font-size: clamp(28px, 4vw, 38px);
    line-height: 1.12;
    letter-spacing: -0.6px;
    transition: color 0.28s ease;
  }
  .deck {
    margin: 0;
    font-size: 17px;
    line-height: 1.55;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .media {
    display: grid;
    place-items: center;
    aspect-ratio: 16 / 10;
    max-height: 260px;
    border-radius: 12px;
    overflow: hidden;
    background: var(--callout);
  }
  @media (min-width: 640px) {
    .media {
      width: 100%;
      max-width: 340px;
      height: auto;
      min-height: 160px;
      max-height: 260px;
      aspect-ratio: 4 / 3;
      justify-self: end;
    }
  }
  .media img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: center;
    filter: saturate(0.92);
    transition: transform 0.55s cubic-bezier(0.22, 1, 0.36, 1), filter 0.45s ease;
  }
  .media.logo img {
    object-fit: contain;
    object-position: center;
    padding: 16%;
    filter: none;
    box-sizing: border-box;
  }
  @media (prefers-reduced-motion: reduce) {
    .title,
    .media img {
      transition: none;
    }
    .lead:hover .media img {
      transform: none;
    }
  }
</style>
