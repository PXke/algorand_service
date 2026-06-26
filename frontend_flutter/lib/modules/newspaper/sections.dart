import 'package:flutter/material.dart';

import '../../core/l10n/l10n_extensions.dart';

/// Editorial sections of the paper. Sections are derived from article tags
/// rather than stored on the article, so a story surfaces in a section when
/// any of its tags matches the section's [keywords].
class NewsSection {
  const NewsSection({
    required this.slug,
    required this.icon,
    required this.keywords,
  });

  final String slug;

  /// Section glyph, used in nav, headers and kickers.
  final IconData icon;

  /// Lower-case tokens that, if present in a story's tags, place it here.
  final Set<String> keywords;

  String label(BuildContext context) {
    final l10n = context.l10n;
    return switch (slug) {
      'markets' => l10n.sectionMarkets,
      'security' => l10n.sectionSecurity,
      'developers' => l10n.sectionDevelopers,
      'community' => l10n.sectionCommunity,
      _ => l10n.sectionEcosystem,
    };
  }
}

// Keyword sets match the display tags produced by the publish pipeline
// (`workers/app/modules/newspaper/article_tags.py`): source kinds, market /
// weekly / digest, publish kinds (discovery, update), and publish topics
// (scam-alert, outage, sdk, community, recap, pricing, breaking).
const List<NewsSection> kNewsSections = [
  NewsSection(
    slug: 'markets',
    icon: Icons.trending_up,
    keywords: {'market', 'pricing', 'price', 'weekly', 'digest'},
  ),
  NewsSection(
    slug: 'security',
    icon: Icons.shield_outlined,
    keywords: {'scam-alert', 'scam', 'outage', 'incident', 'breaking'},
  ),
  NewsSection(
    slug: 'developers',
    icon: Icons.code,
    keywords: {'sdk', 'release', 'ai'},
  ),
  NewsSection(
    slug: 'community',
    icon: Icons.groups_outlined,
    keywords: {'community', 'recap', 'event'},
  ),
  NewsSection(
    slug: 'ecosystem',
    icon: Icons.explore_outlined,
    keywords: {'discovery', 'update', 'new-service', 'launch', 'partnership'},
  ),
];

/// Icon for a section slug, or a sensible default for non-section nav targets.
IconData sectionIcon(String slug) => sectionForSlug(slug)?.icon ?? Icons.article_outlined;

NewsSection? sectionForSlug(String slug) {
  for (final s in kNewsSections) {
    if (s.slug == slug) return s;
  }
  return null;
}

/// Lower-cases tags and returns the first section that matches, if any.
NewsSection? sectionForTags(Iterable<String> tags) {
  final lowered = tags.map((t) => t.toLowerCase().trim()).toSet();
  for (final section in kNewsSections) {
    if (lowered.any(section.keywords.contains)) {
      return section;
    }
  }
  return null;
}

/// Whether a story (by its tags) belongs in the given section.
bool sectionMatches(NewsSection section, Iterable<String> tags) {
  final lowered = tags.map((t) => t.toLowerCase().trim());
  return lowered.any(section.keywords.contains);
}

List<String> tagsOf(Map<String, dynamic> item) {
  return (item['tags'] as List<dynamic>?)
          ?.map((t) => t.toString())
          .where((t) => t.isNotEmpty)
          .toList() ??
      const [];
}
