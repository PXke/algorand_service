import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/page_header.dart';
import 'article_feed_view.dart';

/// A topic landing page (/topic/nft): the feed filtered to one writer tag.
/// Tags are the newsroom's own taxonomy, so this is the sharpest filter the
/// paper has — sections are broader, human-defined groupings.
class TopicPage extends StatelessWidget {
  const TopicPage({super.key, required this.tag});

  final String tag;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final cleaned = tag.trim().toLowerCase();
    return ArticleFeedView(
      key: ValueKey('topic-$cleaned'),
      tag: cleaned,
      header: PageHeader(
        breadcrumb: l10n.navTopics.toUpperCase(),
        title: cleaned,
        subtitle: l10n.topicSubtitle(cleaned),
        // Follow-this-topic loop: every topic has its own full-text RSS feed.
        trailing: IconButton(
          tooltip: 'RSS',
          icon: Icon(Icons.rss_feed, size: 20, color: context.appColors.muted),
          onPressed: () => launchUrl(
            Uri.parse(
                '${Uri.base.origin}/feed/topic/${Uri.encodeComponent(cleaned)}'),
            mode: LaunchMode.externalApplication,
          ),
        ),
      ),
      emptyTitle: l10n.sectionEmptyTitle,
      emptyMessage: l10n.sectionEmptyMessage,
    );
  }
}
