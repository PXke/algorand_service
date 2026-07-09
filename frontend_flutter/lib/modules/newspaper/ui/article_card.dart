import 'package:flutter/material.dart';

import '../../../core/config/app_config.dart';
import '../../../core/ui/lazy_network_image.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/article_tag_chip.dart';
import '../../../core/ui/brand_mark.dart';
import '../../../core/ui/format.dart';
import '../../../core/ui/hover_card.dart';
import '../sections.dart';

/// A story card used across the front page, section pages and the latest feed.
///
/// [hero] gives the lead-story treatment (large serif headline, accent edge);
/// [compact] tightens type and padding for two-column grid rows.
class ArticleCard extends StatelessWidget {
  const ArticleCard({
    super.key,
    required this.item,
    required this.onTap,
    this.hero = false,
    this.compact = false,
  });

  final Map<String, dynamic> item;
  final VoidCallback onTap;
  final bool hero;
  final bool compact;

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

  String _kicker(BuildContext context) {
    final section = sectionForTags(_tags);
    if (section != null) {
      return section.label(context).toUpperCase();
    }
    if (_tags.isNotEmpty) {
      return _tags.first.toUpperCase();
    }
    return context.l10n.navNews.toUpperCase();
  }

  IconData _kickerIcon() => sectionForTags(_tags)?.icon ?? Icons.label_outline;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final title = item['title']?.toString() ?? l10n.articleUntitled;
    final summary = stripMarkdown(item['summary']?.toString() ?? '');
    final epoch = item['published_at_epoch'] as int?;
    final serviceId = item['service_id']?.toString();
    final showRound = _triggerKind == 'chain';
    final kindColor = _kindColor(context);
    final imageUrl = item['image_url']?.toString();
    final hasImage = imageUrl != null && imageUrl.isNotEmpty;

    final hPad = hero ? 30.0 : (compact ? 20.0 : 24.0);
    final vPad = hero ? 26.0 : (compact ? 18.0 : 22.0);
    final titleStyle = hero
        ? theme.textTheme.headlineMedium?.copyWith(height: 1.15)
        : compact
            ? theme.textTheme.titleLarge?.copyWith(fontSize: 19, height: 1.3)
            : theme.textTheme.titleLarge?.copyWith(height: 1.25);

    final content = Padding(
      padding: EdgeInsets.fromLTRB(hPad, vPad - 2, hPad, vPad),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              // Flexible + ellipsis: long localized section labels must shrink
              // the chip, not overflow the row on narrow cards.
              Flexible(
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: kindColor.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(_kickerIcon(), size: 12, color: kindColor),
                      const SizedBox(width: 5),
                      Flexible(
                        child: Text(
                          _kicker(context),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: kindColor,
                            letterSpacing: 0.9,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(width: 8),
              const Spacer(),
              Text(
                formatRelativeEpoch(context, epoch),
                style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
              ),
            ],
          ),
          SizedBox(height: hero ? 16 : 12),
          Text(title, style: titleStyle),
          if (summary.isNotEmpty) ...[
            SizedBox(height: hero ? 14 : 10),
            Text(
              summary,
              maxLines: hero ? 5 : 3,
              overflow: TextOverflow.ellipsis,
              style: (compact ? theme.textTheme.bodyMedium : theme.textTheme.bodyLarge)
                  ?.copyWith(
                height: 1.55,
                color: theme.textTheme.bodyMedium?.color,
              ),
            ),
          ],
          if (compact) const Spacer() else const SizedBox(height: 16),
          if (compact) const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: Text(
                  formatArticleMetaLine(
                    context,
                    publishedEpoch: epoch,
                    round: showRound ? item['trigger_round'] : null,
                    serviceId: serviceId,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
                ),
              ),
              if (!compact && _tags.length > 1) ...[
                const SizedBox(width: 12),
                ArticleTagRow(tags: _tags.sublist(1).take(3).toList(), compact: true),
              ],
            ],
          ),
        ],
      ),
    );

    final sourceUrl = item['source_url']?.toString();
    // Three shapes: a real hero photo (cover-cropped), a brand logo stored as
    // the article image (contained on a tinted wash — cover would blow a small
    // square icon into a cropped, pixelated strip), or no image at all
    // (source-logo / monogram fallback).
    Widget buildImage(double h) => hasImage
        ? (looksLikeLogoUrl(imageUrl)
            ? _LogoFallback(
                height: h,
                kindColor: kindColor,
                sourceUrl: sourceUrl,
                serviceId: serviceId,
                logoUrl: imageUrl,
              )
            : _CardImage(
                url: imageUrl,
                height: h,
                kindColor: kindColor,
                sourceUrl: sourceUrl,
                serviceId: serviceId,
              ))
        : _LogoFallback(
            height: h,
            kindColor: kindColor,
            sourceUrl: sourceUrl,
            serviceId: serviceId,
          );

    return HoverCard(
      onTap: onTap,
      borderRadius: hero ? 18 : 16,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // The lead story uses an OG-shaped frame (~1.9:1, the share-image
          // standard) that scales with the card width, so a 1200x630 og:image
          // fills it with almost no crop instead of being sliced by a fixed,
          // very-wide-and-short strip. Grid tiles keep their compact heights.
          if (hero)
            LayoutBuilder(
              builder: (context, c) =>
                  buildImage((c.maxWidth / 1.9).clamp(200.0, 440.0)),
            )
          else
            buildImage(compact ? 140 : 170),
          content,
        ],
      ),
    );
  }
}

/// True when a stored image is a brand mark rather than a photo:
/// the backend stores the source's icon in image_url when a story has no real
/// share image, and icon files are unmistakable by path. These must be
/// CONTAINED like a logo, never cover-cropped like a hero photo.
/// Shared with [FeedPlacementCard], which has the same failure mode.
bool looksLikeLogoUrl(String url) {
  final path = (Uri.tryParse(url)?.path ?? url).toLowerCase();
  if (path.endsWith('.svg') || path.endsWith('.ico')) return true;
  return RegExp(r'favicon|apple-touch|/icons?[/._-]|[/._-]icons?[._-]|logo')
      .hasMatch(path);
}

/// Feed preview image; fetches lazily so proxied hero art stays off boot.
class _CardImage extends StatelessWidget {
  const _CardImage({
    required this.url,
    required this.height,
    required this.kindColor,
    this.sourceUrl,
    this.serviceId,
  });

  final String url;
  final double height;
  final Color kindColor;
  final String? sourceUrl;
  final String? serviceId;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return LazyNetworkImage(
      url: url,
      height: height,
      width: double.infinity,
      fit: BoxFit.cover,
      placeholder: Container(
        height: height,
        color: colors.border.withValues(alpha: 0.35),
      ),
      error: _LogoFallback(
        height: height,
        kindColor: kindColor,
        sourceUrl: sourceUrl,
        serviceId: serviceId,
      ),
    );
  }
}

/// Branded stand-in shown when a story has no hero image (or it fails to load):
/// the source's logo centered on a faint section-tinted wash, falling back to the
/// PXke monogram when no source logo is available. Mirrors the og:image logo
/// fallback so feed tiles and shared social cards degrade alike.
class _LogoFallback extends StatelessWidget {
  const _LogoFallback({
    required this.height,
    required this.kindColor,
    this.sourceUrl,
    this.serviceId,
    this.logoUrl,
  });

  final double height;
  final Color kindColor;
  final String? sourceUrl;
  final String? serviceId;

  /// When the article's own image_url IS the logo, use it directly instead of
  /// deriving one from the source host.
  final String? logoUrl;

  @override
  Widget build(BuildContext context) {
    final logo =
        logoUrl ?? articleLogoUrl(sourceUrl: sourceUrl, serviceId: serviceId);
    final monogram = BrandMark(size: (height * 0.34).clamp(40.0, 92.0));

    return Container(
      height: height,
      width: double.infinity,
      color: kindColor.withValues(alpha: 0.10),
      alignment: Alignment.center,
      padding: EdgeInsets.symmetric(vertical: height * 0.24),
      child: logo == null
          ? monogram
          : LazyNetworkImage(
              url: logo,
              fit: BoxFit.contain,
              error: monogram,
            ),
    );
  }
}
