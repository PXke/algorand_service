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
        theme_color: '#0A5F59',
        background_color: '#F2F4F2',
        display: 'standalone',
        start_url: '/',
        icons: [
          {
            src: '/icons/icon-192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: '/icons/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
          },
        ],
      },
      workbox: {
        // Precache the reading shell — not the lazy wallet/admin hunks.
        globPatterns: ['**/*.{js,css,html,ico,svg,woff2,png,webp}'],
        globIgnores: ['**/wallet-connect-*.js', '**/AdminHub-*'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/assets\//, /^\/fonts\//],
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
