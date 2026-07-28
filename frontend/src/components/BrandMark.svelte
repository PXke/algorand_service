<script lang="ts">
  let { size = 30 }: { size?: number } = $props()
  const glyph = $derived(size * 0.92)
</script>

<!-- Must stay identical to public/favicon.svg. -->
<span class="mark" style="width:{size}px;height:{size}px" aria-hidden="true">
  <svg viewBox="0 0 32 32" width={glyph} height={glyph} fill="none">
    <path
      fill="currentColor"
      fill-rule="evenodd"
      d="M8 4.6h7.85c4.7 0 7.75 2.65 7.75 6.55s-3.05 6.5-7.75 6.5H12.35V25H8V4.6Zm4.35 3.65v5.75h3.35c2.2 0 3.5-1.1 3.5-2.9s-1.3-2.85-3.5-2.85h-3.35Z"
    />
    <rect y="28" width="32" height="4" fill="#4a5bd0" />
  </svg>
</span>

<style>
  /* A stamp: square corners, ink ground, accent kept to the foot rule. The
     rounded blue chip it replaced read as an app tile beside the nameplate,
     and its three bar-chart bars said nothing about a newspaper. */
  .mark {
    display: inline-grid;
    place-items: center;
    flex-shrink: 0;
    overflow: hidden;
    background: #171410;
    color: #fffdf8;
    user-select: none;
    line-height: 0;
  }
  .mark :global(svg) {
    display: block;
  }
</style>
