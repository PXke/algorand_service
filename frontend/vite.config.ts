import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    svelte(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: false,
      includeAssets: ['favicon.svg', 'icons/*.png', 'fonts/*.woff2', 'offline.html'],
      manifest: {
        name: 'PXke Algorand',
        short_name: 'PXke',
        description: 'Independent coverage of the Algorand ecosystem',
        theme_color: '#eef0f5',
        background_color: '#eef0f5',
        display: 'standalone',
        start_url: '/',
        // Regenerated from public/favicon.svg on 2026-07-27 — the previous PNGs
        // predated the mark rework and still shipped the old blue squircle.
        // The maskable one is its own file: Android crops to a circle, and the
        // square-cornered stamp lost its accent foot rule under that mask, so it
        // is rendered on a padded 40-unit ground to clear the safe zone.
        icons: [
          {
            src: '/icons/icon-128.png',
            sizes: '128x128',
            type: 'image/png',
            purpose: 'any',
          },
          {
            src: '/icons/icon-192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'any',
          },
          {
            src: '/icons/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any',
          },
          {
            src: '/icons/icon-maskable-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Precache the reading shell — not the lazy wallet/admin chunks, the
        // 13 individual admin tab chunks (only the AdminHub shell itself was
        // excluded before, not its lazily-loaded tabs), or the 8 non-English
        // locale bundles (only `en` is on the critical path; the rest load
        // on demand via runtimeCaching below the first time a visitor picks
        // one). The locale glob deliberately excludes `en-*.js` and
        // `es5-*.js` (a legacy-JS polyfill chunk, unrelated to the `es`
        // locale) — verified against a real `dist/assets` listing.
        globPatterns: ['**/*.{js,css,html,ico,svg,woff2,png,webp}'],
        globIgnores: [
          '**/wallet-connect-*.js',
          '**/AdminHub-*',
          '**/*Tab-*.{js,css}',
          '**/{es,fr,zh,ar,ps,fa,ru,hi}-*.js',
        ],
        navigateFallback: '/index.html',
        // Anything the SERVER renders must opt out, or a navigation to it gets
        // the SPA shell handed back by the service worker — which is why
        // feed.xml looked broken in a browser while curl saw perfect XML.
        // Matched by route SHAPE, not file suffix: three of these documents
        // have no extension at all (/feed/topic/:tag, /og/article/:id,
        // /sitemap-articles-:part), so a `\.xml$` test silently missed them.
        // Mirrors register_seo_routes() in backend/app/modules/seo/api/routes.py.
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/assets\//,
          /^\/fonts\//,
          /^\/feed(?:\.xml|\/)/,
          /^\/sitemap/,
          /^\/og\//,
          // robots.txt, llms.txt and the IndexNow key file, all root-level.
          /^\/[^/]+\.txt$/,
          // Writer-captured screenshots and any other nginx-served static
          // media (see deploy/nginx/algorand-platform.conf's /media/
          // alias) — same "curl sees it fine, a browser navigation gets
          // the SPA shell" bug as feed.xml above, hit 2026-08-26 when a
          // captured screenshot's own image_url 404'd (SPA "not found")
          // on direct navigation despite the file existing and nginx
          // serving it correctly.
          /^\/media\//,
        ],
        runtimeCaching: [
          {
            urlPattern: ({ request }) => request.mode === 'navigate',
            handler: 'NetworkFirst',
            options: {
              cacheName: 'pages',
              networkTimeoutSeconds: 4,
              plugins: [
                {
                  handlerDidError: async () => {
                    return (await caches.match('/offline.html')) || Response.error()
                  },
                },
              ],
            },
          },
          {
            // Non-English locale bundles: excluded from the precache above
            // (globIgnores) so no visitor pays for 8 languages they'll never
            // read, but content-hashed and immutable once fetched — cache
            // them the first time a visitor actually switches locale.
            urlPattern: ({ url }) =>
              /\/assets\/(?:es|fr|zh|ar|ps|fa|ru|hi)-[\w-]+\.js$/.test(url.pathname),
            handler: 'CacheFirst',
            options: {
              cacheName: 'locales',
              expiration: { maxEntries: 8 },
            },
          },
        ],
      },
    }),
  ],
  // WalletConnect / crypto deps still expect Node globals.
  define: {
    global: 'globalThis',
  },
  resolve: {
    alias: {
      buffer: 'buffer/',
    },
  },
  optimizeDeps: {
    include: ['@walletconnect/client', 'algosdk', 'qrcode', 'buffer'],
  },
  build: {
    // Modern browsers only — smaller transforms, no legacy polyfill tax.
    target: 'es2022',
    cssMinify: true,
    modulePreload: { polyfill: false },
    // wallet-connect now also absorbs Pera's/Defly's/Lute's own SDK bundles
    // (see codeSplitting groups below) — expected to sit around ~1.6 MB.
    // It's a lazy, on-demand chunk excluded from the PWA precache, so its
    // size doesn't affect first load; raised only to stop this warning.
    chunkSizeWarningLimit: 1700,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'wallet-connect',
              test: /node_modules[\\/](@walletconnect|algosdk|tweetnacl|qrcode|buffer|@perawallet|@blockshake|@galaxypay)/,
            },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_API || 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
})
