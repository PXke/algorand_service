<script lang="ts">
  let { size = 30 }: { size?: number } = $props()
  const radius = $derived(size * 0.23)
  const fontSize = $derived(size * 0.6)
</script>

<span
  class="mark"
  style="width:{size}px;height:{size}px;border-radius:{radius}px;font-size:{fontSize}px"
  aria-hidden="true">P</span
>

<style>
  .mark {
    display: inline-grid;
    place-items: center;
    flex-shrink: 0;
    background: linear-gradient(145deg, #4338ca 0%, #6366f1 100%);
    color: #fff;
    font-family: var(--font-sans);
    font-weight: 800;
    line-height: 1;
    user-select: none;
  }
</style>
