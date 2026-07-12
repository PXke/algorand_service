import 'package:flutter/material.dart';

import '../../../core/config/app_config.dart';
import '../../../core/ui/lazy_network_image.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/article_tag_chip.dart';
import '../../../core/ui/format.dart';
import '../sections.dart';

/// The lead-story package (borderless broadsheet treatment). Every other
/// story listing uses StoryRow — card tiles were retired 2026-07 so the
/// paper reads as one flat editorial system.
class ArticleCard extends StatelessWidget {
  const ArticleCard({
    super.key,
    required this.item,
    required this.onTap,
    this.hero = true,
  });

  final Map<String, dynamic> item;
  final VoidCallback onTap;

  /// Kept for call-site compatibility; the lead treatment is the only one.
  final bool hero;

  List<String> get _tags => tagsOf(item);

  String get _triggerKind => item['trigger_kind']?.toString() ?? 'editorial';

  Color _kindColor(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    return switch (_triggerKind) {
      'chain' => isDark ? const Color(0xFF7FC0B8) : const Color(0xFF0E7C70),
      'scheduled' => isDark ? const Color(0xFFA99BCB) : const Color(0xFF6B46C1),
      _ => theme.colorScheme.primary,
    };
  }

  /// The story's primary writer tag — the paper's real taxonomy (the fixed
  /// human sections were retired; LLM tags are more accurate). Boilerplate
  /// tags ("web", "news"…) are skipped so the kicker says something.
  String _kicker(BuildContext context) {
    final primary = primaryTag(_tags);
    if (primary != null) return displayTagLabel(primary).toUpperCase();
    return context.l10n.navNews.toUpperCase();
  }

  /// Tags for the chip row below the meta line, minus the one the kicker
  /// already displays.
  List<String> get _secondaryTags {
    final primary = primaryTag(_tags);
    if (primary == null) return _tags;
    final rest = List.of(_tags)..remove(primary);
    return rest;
  }

  @override
  Widget build(BuildContext context) {
    return _HeroLead(
      item: item,
      onTap: onTap,
      kindColor: _kindColor(context),
      kicker: _kicker(context),
      secondaryTags: _secondaryTags,
      showRound: _triggerKind == 'chain',
    );
  }
}

/// The front page's lead story: a borderless broadsheet package that sits
/// directly on the paper background. Text carries the weight — accent slug,
/// small-caps kicker, display-serif headline, deck, meta — with the artwork
/// set beside the text on wide screens. When the story has no real photo
/// (logo-only or nothing), the package stays text-only instead of showing a
/// giant tinted void.
class _HeroLead extends StatefulWidget {
  const _HeroLead({
    required this.item,
    required this.onTap,
    required this.kindColor,
    required this.kicker,
    required this.secondaryTags,
    required this.showRound,
  });

  final Map<String, dynamic> item;
  final VoidCallback onTap;
  final Color kindColor;
  final String kicker;
  final List<String> secondaryTags;
  final bool showRound;

  @override
  State<_HeroLead> createState() => _HeroLeadState();
}

class _HeroLeadState extends State<_HeroLead> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final item = widget.item;
    final title = item['title']?.toString() ?? l10n.articleUntitled;
    final summary = stripMarkdown(item['summary']?.toString() ?? '');
    final epoch = item['published_at_epoch'] as int?;
    final serviceId = item['service_id']?.toString();
    final imageUrl = item['image_url']?.toString();
    final hasPhoto = imageUrl != null &&
        imageUrl.isNotEmpty &&
        !looksLikeLogoUrl(imageUrl);

    return LayoutBuilder(
      builder: (context, c) {
        final wide = c.maxWidth >= 640;
        final showSideImage = hasPhoto && wide;

        final headlineStyle = (wide
                ? theme.textTheme.displaySmall?.copyWith(fontSize: 38)
                : theme.textTheme.headlineMedium)
            ?.copyWith(
          height: 1.12,
          letterSpacing: -0.6,
          color: _hovered ? theme.colorScheme.primary : null,
        );

        final textColumn = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Lead slug: the one thick accent mark on the page.
            Container(width: 34, height: 3, color: colors.accent),
            const SizedBox(height: 14),
            Row(
              children: [
                Flexible(
                  child: Text(
                    widget.kicker,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: widget.kindColor,
                      letterSpacing: 1.1,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Text(
                  formatRelativeEpoch(context, epoch),
                  style:
                      theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
                ),
              ],
            ),
            const SizedBox(height: 12),
            AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 140),
              style: headlineStyle ?? const TextStyle(),
              child: Text(title),
            ),
            if (summary.isNotEmpty) ...[
              const SizedBox(height: 14),
              Text(
                summary,
                maxLines: wide ? 3 : 4,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyLarge?.copyWith(
                  height: 1.55,
                  color: colors.muted,
                  fontSize: 17,
                ),
              ),
            ],
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: Text(
                    formatArticleMetaLine(
                      context,
                      round: widget.showRound ? item['trigger_round'] : null,
                      serviceId: serviceId,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        theme.textTheme.bodySmall?.copyWith(color: colors.muted),
                  ),
                ),
                if (widget.secondaryTags.isNotEmpty) ...[
                  const SizedBox(width: 12),
                  ArticleTagRow(
                    tags: widget.secondaryTags.take(3).toList(),
                    compact: true,
                  ),
                ],
              ],
            ),
          ],
        );

        final body = showSideImage
            ? Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: textColumn),
                  const SizedBox(width: 28),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: SizedBox(
                      width: (c.maxWidth * 0.38).clamp(240.0, 340.0),
                      child: ColorFiltered(
                        colorFilter: editorialThumbFilter,
                        child: LazyNetworkImage(
                          url: imageUrl,
                          height: 224,
                          width: double.infinity,
                          fit: BoxFit.cover,
                          placeholder: Container(
                            height: 224,
                            color: colors.border.withValues(alpha: 0.35),
                          ),
                          error: const SizedBox.shrink(),
                        ),
                      ),
                    ),
                  ),
                ],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (hasPhoto) ...[
                    ClipRRect(
                      borderRadius: BorderRadius.circular(12),
                      child: ColorFiltered(
                        colorFilter: editorialThumbFilter,
                        child: LazyNetworkImage(
                          url: imageUrl,
                          height: 200,
                          width: double.infinity,
                          fit: BoxFit.cover,
                          placeholder: Container(
                            height: 200,
                            color: colors.border.withValues(alpha: 0.35),
                          ),
                          error: const SizedBox.shrink(),
                        ),
                      ),
                    ),
                    const SizedBox(height: 18),
                  ],
                  textColumn,
                ],
              );

        return MouseRegion(
          onEnter: (_) => setState(() => _hovered = true),
          onExit: (_) => setState(() => _hovered = false),
          cursor: SystemMouseCursors.click,
          child: GestureDetector(
            onTap: widget.onTap,
            behavior: HitTestBehavior.opaque,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: body,
            ),
          ),
        );
      },
    );
  }
}
