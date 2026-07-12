/// Tag utilities for the newspaper. The paper's taxonomy IS the writer's own
/// tags (surfaced as /topics and /topic/:tag); the old fixed sections — a
/// human-defined keyword mapping over those tags — were retired 2026-07
/// because the LLM tags are more accurate than the hand grouping.
library;

/// Reader-facing label for a raw pipeline tag used as a kicker. Most tags read
/// fine as-is; internal jargon gets translated.
String displayTagLabel(String tag) {
  return switch (tag.toLowerCase().trim()) {
    'chain-only' => 'on-chain',
    _ => tag,
  };
}

/// Boilerplate tags the pipeline stamps on most stories; they carry no
/// topical signal, so kickers and breadcrumbs skip past them.
const Set<String> kBoilerplateTags = {
  'web',
  'news',
  'discovery',
  'algorand',
  'generic',
  'service',
};

/// The story's primary topical tag: the first tag that isn't boilerplate,
/// falling back to the plain first tag when every label is generic.
String? primaryTag(List<String> tags) {
  for (final tag in tags) {
    if (!kBoilerplateTags.contains(tag.toLowerCase().trim())) return tag;
  }
  return tags.isEmpty ? null : tags.first;
}

List<String> tagsOf(Map<String, dynamic> item) {
  return (item['tags'] as List<dynamic>?)
          ?.map((t) => t.toString())
          .where((t) => t.isNotEmpty)
          .toList() ??
      const [];
}
