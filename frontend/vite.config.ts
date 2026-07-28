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
        theme_color: '#3a49ad',
        background_color: '#F7F4EE',
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
        // Precache the reading shell — not the lazy wallet/admin hunks.
        globPatterns: ['**/*.{js,css,html,ico,svg,woff2,png,webp}'],
        globIgnores: ['**/wallet-connect-*.js', '**/AdminHub-*'],
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
    chunkSizeWarningLimit: 900,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'wallet-connect',
              test: /node_modules[\\/](@walletconnect|algosdk|tweetnacl|qrcode|buffer)/,
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
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
})
