<script lang="ts">
  let { size = 30 }: { size?: number } = $props()
  const radius = $derived(Math.max(7, size * 0.26))
  const glyph = $derived(size * 0.9)
</script>

<span
  class="mark"
  style="width:{size}px;height:{size}px;border-radius:{radius}px"
  aria-hidden="true"
>
  <svg viewBox="0 0 32 32" width={glyph} height={glyph} fill="none">
    <path
      fill="currentColor"
      fill-rule="evenodd"
      d="M8 5.8h7.85c4.7 0 7.75 2.65 7.75 6.55s-3.05 6.5-7.75 6.5H12.35V26.2H8V5.8Zm4.35 3.65v5.75h3.35c2.2 0 3.5-1.1 3.5-2.9s-1.3-2.85-3.5-2.85h-3.35Z"
    />
    <rect x="19.4" y="20.15" width="2.4" height="2.15" rx="1.05" fill="currentColor" />
    <rect x="19.4" y="23.05" width="4" height="2.15" rx="1.05" fill="currentColor" />
    <rect x="19.4" y="25.95" width="5.6" height="2.15" rx="1.05" fill="currentColor" />
  </svg>
</span>

<style>
  .mark {
    display: inline-grid;
    place-items: center;
    flex-shrink: 0;
    background: #4a5bd0;
    color: #fff;
    user-select: none;
    line-height: 0;
  }
  .mark :global(svg) {
    display: block;
  }
</style>
