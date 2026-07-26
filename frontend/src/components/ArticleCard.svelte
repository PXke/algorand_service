<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import StoryRow from './StoryRow.svelte'

  /** @deprecated Prefer StoryRow / LeadStory — kept as thin alias. */
  let { article }: { article: ArticleItem } = $props()
</script>

<StoryRow {article} />
