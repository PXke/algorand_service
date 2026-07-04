import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:url_launcher/url_launcher.dart';

import '../theme/app_theme_extension.dart';
import 'glossary.dart';
import 'lazy_network_image.dart';

/// Renders article body markdown as long-form editorial prose: serif body
/// copy, generous leading, and a clear heading hierarchy. No card frame —
/// the article reads like a page, not a panel.
class ArticleMarkdown extends StatelessWidget {
  const ArticleMarkdown({
    super.key,
    required this.data,
    this.selectable = true,
  });

  final String data;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    if (data.trim().isEmpty) {
      return const SizedBox.shrink();
    }

    final theme = Theme.of(context);
    final colors = context.appColors;
    final scheme = theme.colorScheme;
    final isDark = theme.brightness == Brightness.dark;
    final ink = isDark ? const Color(0xFFE6EAF1) : const Color(0xFF1F242D);
    final muted = theme.textTheme.bodySmall?.color ?? colors.muted;

    // Slightly larger, more open setting on desktop; a touch tighter on phones
    // where the column is naturally narrow. Sustained-reading sizes.
    final isMobile = MediaQuery.sizeOf(context).width < 520;
    final bodySize = isMobile ? 17.5 : 19.0;
    final bodyLeading = isMobile ? 1.72 : 1.8;

    // Inter body on web keeps the font manifest lean (serif w700 for headlines).
    final body = TextStyle(
      fontSize: bodySize,
      height: bodyLeading,
      color: ink,
      fontWeight: FontWeight.w400,
    );
    final caption = theme.textTheme.bodyMedium?.copyWith(height: 1.55, color: ink);

    TextStyle heading(double size, {double spacing = -0.3}) => TextStyle(
          fontFamily: 'Source Serif 4',
          fontSize: size,
          height: 1.25,
          color: ink,
          fontWeight: FontWeight.w700,
          letterSpacing: spacing,
        );

    final styleSheet = MarkdownStyleSheet.fromTheme(theme).copyWith(
      p: body,
      pPadding: const EdgeInsets.only(bottom: 18),
      h1: heading(30, spacing: -0.5),
      h1Padding: const EdgeInsets.only(top: 12, bottom: 16),
      h2: heading(24, spacing: -0.4),
      h2Padding: const EdgeInsets.only(top: 34, bottom: 12),
      h3: heading(20),
      h3Padding: const EdgeInsets.only(top: 26, bottom: 10),
      h4: theme.textTheme.titleSmall?.copyWith(
        fontWeight: FontWeight.w700,
        letterSpacing: 0.4,
        color: muted,
      ),
      h4Padding: const EdgeInsets.only(top: 20, bottom: 8),
      listBullet: body,
      listIndent: 28,
      listBulletPadding: const EdgeInsets.only(bottom: 10),
      blockSpacing: 14,
      blockquote: body.copyWith(
        fontSize: 17,
        color: muted,
        fontStyle: FontStyle.italic,
      ),
      blockquoteDecoration: BoxDecoration(
        border: Border(
          left: BorderSide(color: colors.accent.withValues(alpha: 0.55), width: 3),
        ),
      ),
      blockquotePadding: const EdgeInsets.fromLTRB(18, 4, 8, 4),
      code: theme.textTheme.bodyMedium?.copyWith(
        fontFamily: 'monospace',
        fontSize: 14.5,
        color: isDark ? const Color(0xFFA7C5FF) : const Color(0xFF1A4AA0),
        backgroundColor: scheme.surfaceContainerHighest,
      ),
      codeblockDecoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: colors.border),
      ),
      codeblockPadding: const EdgeInsets.all(16),
      horizontalRuleDecoration: BoxDecoration(
        border: Border(top: BorderSide(color: colors.border, width: 1)),
      ),
      tableHead: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700),
      tableBody: caption,
      tableCellsPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      tableBorder: TableBorder.all(color: colors.border),
      a: body.copyWith(
        color: colors.accent,
        decoration: TextDecoration.underline,
        decorationColor: colors.accent.withValues(alpha: 0.35),
        decorationThickness: 1.4,
      ),
      strong: body.copyWith(fontWeight: FontWeight.w700),
      em: body.copyWith(fontStyle: FontStyle.italic),
    );

    final markdown = MarkdownBody(
      data: data,
      // Selection is handled by the SelectionArea below, not MarkdownBody's own
      // SelectableText path — that path is unreliable on Flutter web (CanvasKit).
      selectable: false,
      styleSheet: styleSheet,
      // Glossary: mark the first occurrence of known Algorand/DeFi jargon with a
      // dotted underline + hover/long-press definition tooltip. A fresh syntax
      // instance per build resets the "first occurrence only" tracking.
      inlineSyntaxes: [GlossaryInlineSyntax()],
      builders: {'glossary': GlossaryElementBuilder(accent: colors.accent)},
      // Route body images (hero + inline) through the same-origin proxy so
      // CanvasKit can render cross-origin sources that omit CORS headers.
      imageBuilder: (uri, title, alt) => LazyNetworkImage(
        url: uri.toString(),
        fit: BoxFit.cover,
        error: const SizedBox.shrink(),
      ),
      onTapLink: (text, href, title) {
        final url = href ?? text;
        final uri = Uri.tryParse(url);
        if (uri == null) return;
        launchUrl(uri, mode: LaunchMode.externalApplication);
      },
    );

    // SelectionArea makes the prose selectable on web (Firefox + Chrome), where
    // MarkdownBody's own `selectable` flag does not reliably work.
    return selectable ? SelectionArea(child: markdown) : markdown;
  }
}
