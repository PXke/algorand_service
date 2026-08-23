<script lang="ts">
  import {
    SITE_NAME,
    absoluteUrl,
    formatPageTitle,
    safeJsonLd,
  } from '../lib/seo'

  let {
    title,
    description = '',
    /** Site-relative path or absolute URL for canonical / og:url. */
    path = '',
    ogType = 'website',
    image = '',
    imageAlt = '',
    ogLocale = 'en_US',
    jsonLd = null as Record<string, unknown> | Record<string, unknown>[] | null,
    /** When false, `title` is used as-is (already formatted). */
    brandTitle = true,
    /** When true, emit noindex,follow (utility / duplicate / thin pages). */
    noindex = false,
  }: {
    title: string
    description?: string
    path?: string
    ogType?: 'website' | 'article'
    image?: string
    imageAlt?: string
    ogLocale?: string
    jsonLd?: Record<string, unknown> | Record<string, unknown>[] | null
    brandTitle?: boolean
    noindex?: boolean
  } = $props()

  const docTitle = $derived(brandTitle ? formatPageTitle(title) : title)
  const canonical = $derived(
    absoluteUrl(
      path ||
        (typeof window !== 'undefined'
          ? `${window.location.pathname}${window.location.search}`
          : '/'),
    ),
  )
  const imageUrl = $derived(image ? absoluteUrl(image) : '')
  const jsonLdHtml = $derived.by(() => {
    if (!jsonLd) return ''
    const open = '<scr' + 'ipt type="application/ld+json">'
    const close = '</scr' + 'ipt>'
    return open + safeJsonLd(jsonLd) + close
  })
</script>

<svelte:head>
  <title>{docTitle}</title>
  {#if description}
    <meta name="description" content={description} />
  {/if}
  <link rel="canonical" href={canonical} />
  <meta name="robots" content={noindex ? 'noindex, follow' : 'max-image-preview:large'} />

  <meta property="og:title" content={title || SITE_NAME} />
  {#if description}
    <meta property="og:description" content={description} />
  {/if}
  <meta property="og:url" content={canonical} />
  <meta property="og:type" content={ogType} />
  <meta property="og:site_name" content={SITE_NAME} />
  <meta property="og:locale" content={ogLocale} />
  {#if imageUrl}
    <meta property="og:image" content={imageUrl} />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    {#if imageAlt}
      <meta property="og:image:alt" content={imageAlt} />
    {/if}
  {/if}

  <meta name="twitter:card" content={imageUrl ? 'summary_large_image' : 'summary'} />
  <meta name="twitter:title" content={title || SITE_NAME} />
  {#if description}
    <meta name="twitter:description" content={description} />
  {/if}
  {#if imageUrl}
    <meta name="twitter:image" content={imageUrl} />
    {#if imageAlt}
      <meta name="twitter:image:alt" content={imageAlt} />
    {/if}
  {/if}

  {#if jsonLdHtml}
    {@html jsonLdHtml}
  {/if}
</svelte:head>
