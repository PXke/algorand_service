<script lang="ts">
  let { size = 30 }: { size?: number } = $props()
  const radius = $derived(Math.max(6, size * 0.22))
</script>

<span
  class="mark"
  style="width:{size}px;height:{size}px;border-radius:{radius}px"
  aria-hidden="true"
>
  <svg viewBox="0 0 32 32" width={size * 0.62} height={size * 0.62} fill="none">
    <path
      d="M16 4.5 27 27.5H21.4L16 15.2 10.6 27.5H5L16 4.5Z"
      fill="currentColor"
    />
    <path d="M12.2 21.2h7.6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
  </svg>
</span>

<style>
  .mark {
    display: inline-grid;
    place-items: center;
    flex-shrink: 0;
    background:
      radial-gradient(120% 100% at 20% 15%, #2bb5a8 0%, transparent 55%),
      linear-gradient(145deg, #0a5f59 0%, #148f86 55%, #0a4a45 100%);
    color: #fff;
    box-shadow: inset 0 1px 0 color-mix(in srgb, #fff 22%, transparent);
    user-select: none;
  }
</style>
