/** First-party opt-out cookie checked by the pageview beacon and API. */
const COOKIE = 'pxke_no_track'

/**
 * When an admin wallet is connected, set `pxke_no_track=1` so our own visits
 * are not counted (matches Flutter `_AdminAnalyticsOptOut`).
 */
export function setAnalyticsOptOut(enabled: boolean): void {
  if (typeof document === 'undefined') return
  const host = window.location.hostname
  const domain =
    host === 'pxke.me' || host.endsWith('.pxke.me') ? '; domain=.pxke.me' : ''
  document.cookie = enabled
    ? `${COOKIE}=1; path=/${domain}; max-age=31536000; SameSite=Lax`
    : `${COOKIE}=; path=/${domain}; max-age=0; SameSite=Lax`
}
