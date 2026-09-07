/// <reference lib="webworker" />
import { defineConfig, loadEnv } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { VitePWA } from 'vite-plugin-pwa'

// Regenerated from public/favicon.svg on 2026-07-27 — the previous PNGs
// predated the mark rework and still shipped the old blue squircle. The
// maskable one is its own file: Android crops to a circle, and the
// square-cornered stamp lost its accent foot rule under that mask, so it is
// rendered on a padded 40-unit ground to clear the safe zone. Shared by both
// products below — same brand mark, only the manifest text differs.
const PWA_ICONS = [
  { src: '/icons/icon-128.png', sizes: '128x128', type: 'image/png', purpose: 'any' as const },
  { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' as const },
  { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' as const },
  {
    src: '/icons/icon-maskable-512.png',
    sizes: '512x512',
    type: 'image/png',
    purpose: 'maskable' as const,
  },
]

export default defineConfig(({ mode }) => {
  // VITE_PRODUCT drives which SPA this build is: 'news' (default,
  // algorand.pxke.me), 'marketplace' (x402.pxke.me), or 'registry'
  // (algorand-registry.pxke.me) — one codebase, three build outputs, per
  // docs/x402-marketplace-product-redesign.md §3.1 row 7 and the same
  // pattern applied a third time for the Algorand Open Registry.
  // loadEnv also sees real process.env (deploy sets this directly around the
  // `vite build` invocation, per write_vite_env.sh's existing FRONTEND_*
  // convention), not just .env files, so `VITE_PRODUCT=marketplace vite
  // build` and an `.env.production.local` both work.
  const env = loadEnv(mode, process.cwd(), '')
  const isMarketplace = env.VITE_PRODUCT === 'marketplace'
  const isRegistry = env.VITE_PRODUCT === 'registry'

  return {
    plugins: [
      svelte(),
      VitePWA({
        registerType: 'autoUpdate',
        injectRegister: false,
        includeAssets: ['favicon.svg', 'icons/*.png', 'fonts/*.woff2', 'offline.html'],
        manifest: isMarketplace
          ? {
              name: 'PXke x402',
              short_name: 'PXke x402',
              description: 'x402 endpoints on Algorand: list yours, find others, pay per call.',
              theme_color: '#eef0f5',
              background_color: '#eef0f5',
              display: 'standalone',
              start_url: '/',
              icons: PWA_ICONS,
            }
          : isRegistry
          ? {
              name: 'PXke Algorand Registry',
              short_name: 'Registry',
              description: 'A free, human-reviewed directory of Algorand ecosystem projects.',
              theme_color: '#eef0f5',
              background_color: '#eef0f5',
              display: 'standalone',
              start_url: '/',
              icons: PWA_ICONS,
            }
          : {
              name: 'PXke Algorand',
              short_name: 'PXke',
              description: 'Independent coverage of the Algorand ecosystem',
              theme_color: '#eef0f5',
              background_color: '#eef0f5',
              display: 'standalone',
              start_url: '/',
              icons: PWA_ICONS,
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
      // Separate output dirs so `npm run build`, `npm run build:marketplace`
      // and `npm run build:registry` can run back to back without one
      // clobbering another — deploy ships whichever dir(s) it needs from
      // deploy/, not decided here.
      outDir: isMarketplace ? 'dist-marketplace' : isRegistry ? 'dist-registry' : 'dist',
      // Modern browsers only — smaller transforms, no legacy polyfill tax.
      target: 'es2022',
      cssMinify: true,
      modulePreload: { polyfill: false },
      // 'hidden': emit real .map files (a prerequisite for Bugsnag to
      // deobfuscate minified stack traces — see lib/bugsnag.ts) without
      // adding a `//# sourceMappingURL` comment to the shipped JS, so a
      // visitor's browser never auto-fetches them. Source isn't otherwise
      // secret (this repo is public), so serving the .map files themselves
      // is not a new exposure. NOTE: generating the files is only half the
      // wiring — nothing here uploads them to Bugsnag yet; see the report /
      // lib/bugsnag.ts for the deploy-side step still needed.
      sourcemap: 'hidden',
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
  }
})
