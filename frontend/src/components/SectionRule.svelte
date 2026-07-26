<script lang="ts">
  let {
    label,
    href = undefined,
  }: {
    label: string
    href?: string
  } = $props()

  import { navigate } from '../lib/router'
</script>

<div class="section-rule">
  <span class="accent-slug"></span>
  {#if href}
    <a
      class="label"
      {href}
      onclick={(e) => {
        e.preventDefault()
        navigate(href)
      }}>{label}</a
    >
  {:else}
    <p class="label">{label}</p>
  {/if}
  <hr class="rule" />
</div>

<style>
  a.label {
    color: inherit;
    text-decoration: none;
  }
  a.label:hover {
    color: var(--primary);
    text-decoration: none;
  }
</style>
