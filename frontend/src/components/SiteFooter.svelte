<script lang="ts">
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import BrandMark from './BrandMark.svelte'

  const year = String(new Date().getFullYear())

  function go(href: string, e: MouseEvent) {
    e.preventDefault()
    navigate(href)
  }
</script>

<footer class="site-footer">
  <div class="inner">
    <div class="grid">
      <div class="brand">
        <div class="nameplate">
          <BrandMark size={30} />
          <strong class="title">{t($messages, 'appTitle')}</strong>
        </div>
        <p class="tagline muted">{t($messages, 'footerTagline')}</p>
      </div>

      <div class="cols">
        <div class="col news">
          <p class="heading">{t($messages, 'navNews')}</p>
          <div class="links split">
            <div>
              <a href="/news" onclick={(e) => go('/news', e)}>{t($messages, 'navLatest')}</a>
              <a href="/hot" onclick={(e) => go('/hot', e)}>{t($messages, 'hotTitle')}</a>
            </div>
            <div>
              <a href="/topics" onclick={(e) => go('/topics', e)}>{t($messages, 'navTopics')}</a>
              <a href="/feed.xml" rel="noopener">RSS</a>
            </div>
          </div>
        </div>

        <div class="col">
          <p class="heading">{t($messages, 'footerAboutHeading')}</p>
          <div class="links">
            <a href="/about" onclick={(e) => go('/about', e)}>{t($messages, 'navAbout')}</a>
            <a href="/contact" onclick={(e) => go('/contact', e)}>{t($messages, 'navContact')}</a>
            <a href="/glossary" onclick={(e) => go('/glossary', e)}>{t($messages, 'navGlossary')}</a>
          </div>
        </div>

        <div class="col">
          <p class="heading">{t($messages, 'footerFollowHeading')}</p>
          <div class="links">
            <a
              href="https://bsky.app/profile/algorand.pxke.me"
              target="_blank"
              rel="noopener noreferrer">Bluesky</a
            >
            <a href="https://t.me/PXkeAlgorandNews" target="_blank" rel="noopener noreferrer"
              >Telegram</a
            >
          </div>
        </div>
      </div>
    </div>

    <hr class="rule" />
    <div class="colophon">
      <p class="rights">{t($messages, 'footerRights', { year })}</p>
    </div>
  </div>
</footer>

<style>
  .site-footer {
    margin-top: auto;
    border-top: 1px solid var(--border);
    background: var(--surface);
    color: var(--on-surface);
  }
  .inner {
    max-width: var(--max-wide);
    margin: 0 auto;
    padding: 40px var(--shell-gutter) 32px;
  }
  @media (max-width: 519px) {
    .inner {
      padding: 18px 16px calc(14px + env(safe-area-inset-bottom, 0px));
    }
    .grid {
      gap: 16px;
    }
    .cols {
      gap: 12px;
    }
    .tagline {
      margin-top: 6px;
      font-size: 0.85rem;
    }
    .heading {
      margin-bottom: 6px;
    }
    .links a {
      min-height: 32px;
      padding: 4px 2px;
      font-size: 0.9rem;
    }
    .rule {
      margin: 16px 0 10px;
    }
  }
  .grid {
    display: flex;
    flex-direction: column;
    gap: 28px;
  }
  @media (min-width: 720px) {
    .grid {
      flex-direction: row;
      gap: 48px;
      align-items: flex-start;
    }
    .brand {
      flex: 5;
      min-width: 0;
    }
    .cols {
      flex: 7;
      display: flex;
      gap: 48px;
      align-items: flex-start;
    }
  }
  .nameplate {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }
  .title {
    font-family: var(--font-display);
    font-size: 1.25rem;
    font-weight: 700;
    letter-spacing: -0.3px;
    color: var(--on-surface);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tagline {
    margin: 8px 0 0;
    max-width: 28rem;
    font-size: 0.9rem;
    line-height: 1.5;
  }
  .cols {
    display: flex;
    flex-direction: column;
    gap: 24px;
  }
  @media (min-width: 720px) {
    .cols {
      flex-direction: row;
    }
    .col.news {
      flex: 1;
    }
  }
  .heading {
    margin: 0 0 12px;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.9px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .heading::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 9px;
    background: var(--accent);
    vertical-align: 6%;
  }
  .links {
    display: flex;
    flex-direction: column;
  }
  .links.split {
    flex-direction: column;
    gap: 0;
  }
  @media (min-width: 720px) {
    .links.split {
      flex-direction: row;
      gap: 40px;
    }
  }
  .links a {
    display: flex;
    align-items: center;
    min-height: 44px;
    padding: 8px 4px;
    color: var(--on-surface);
    text-decoration: none;
    font-size: 0.95rem;
    border-radius: 6px;
  }
  .links a:hover {
    color: var(--primary);
    text-decoration: none;
  }
  .rule {
    border: 0;
    border-top: 1px solid var(--border);
    margin: 28px 0 16px;
  }
  .rights {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.2px;
    color: var(--muted);
  }
  .colophon {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px 24px;
  }
</style>
