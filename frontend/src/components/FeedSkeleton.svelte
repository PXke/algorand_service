<script lang="ts">
  let {
    rows = 6,
    lead = false,
  }: {
    rows?: number
    lead?: boolean
  } = $props()
</script>

<div class="skel" aria-hidden="true">
  {#if lead}
    <div class="lead">
      <div class="copy">
        <span class="bar slug"></span>
        <span class="bar title"></span>
        <span class="bar title short"></span>
        <span class="bar deck"></span>
        <span class="bar deck mid"></span>
      </div>
      <div class="media"></div>
    </div>
    <hr class="hairline" />
  {/if}
  {#each Array.from({ length: rows }, (_, i) => i) as i}
    <div class="row" class:dense={i < 4 && lead}>
      <div class="text">
        <span class="bar meta"></span>
        <span class="bar line"></span>
        <span class="bar line short"></span>
      </div>
      <div class="thumb"></div>
    </div>
  {/each}
</div>

<style>
  .skel {
    display: flex;
    flex-direction: column;
    gap: 0;
  }
  .bar {
    display: block;
    height: 12px;
    border-radius: 4px;
    background: linear-gradient(
      90deg,
      color-mix(in srgb, var(--border) 70%, transparent) 0%,
      color-mix(in srgb, var(--accent) 10%, var(--callout)) 50%,
      color-mix(in srgb, var(--border) 70%, transparent) 100%
    );
    background-size: 200% 100%;
    animation: shimmer 1.2s ease-in-out infinite;
  }
  .lead {
    display: grid;
    gap: 18px;
    padding-bottom: 12px;
  }
  @media (min-width: 640px) {
    .lead {
      grid-template-columns: 1fr minmax(240px, 38%);
    }
  }
  .copy {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .slug {
    width: 34px;
    height: 3px;
  }
  .title {
    height: 28px;
    width: 92%;
  }
  .title.short {
    width: 64%;
  }
  .deck {
    height: 14px;
    width: 100%;
  }
  .deck.mid {
    width: 78%;
  }
  .media {
    aspect-ratio: 4 / 3;
    max-height: 220px;
    border-radius: 12px;
    background: var(--callout);
    animation: pulse 1.2s ease-in-out infinite;
  }
  .row {
    display: grid;
    grid-template-columns: 1fr 88px;
    gap: 14px;
    padding: 16px 0;
    border-bottom: 1px solid var(--border);
  }
  .row.dense {
    grid-template-columns: 1fr 64px;
    padding: 14px 0;
  }
  .text {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }
  .meta {
    width: 40%;
    height: 10px;
  }
  .line {
    width: 95%;
    height: 14px;
  }
  .line.short {
    width: 55%;
  }
  .thumb {
    width: 88px;
    height: 88px;
    border-radius: var(--radius-thumb);
    background: var(--callout);
    animation: pulse 1.2s ease-in-out infinite;
  }
  .dense .thumb {
    width: 64px;
    height: 64px;
  }
  @keyframes shimmer {
    0% {
      background-position: 100% 0;
    }
    100% {
      background-position: -100% 0;
    }
  }
  @keyframes pulse {
    0%,
    100% {
      opacity: 0.55;
    }
    50% {
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .bar,
    .media,
    .thumb {
      animation: none;
    }
  }
</style>
