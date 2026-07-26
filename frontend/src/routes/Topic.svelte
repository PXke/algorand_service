<script lang="ts">
  import News from './News.svelte'

  let { tag }: { tag: string } = $props()
</script>

<News {tag} title={`#${tag}`} />
