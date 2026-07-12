import 'package:flutter/material.dart';

/// Renders Typesense highlight snippets that wrap matches in `<mark>…</mark>`.
class HighlightText extends StatelessWidget {
  const HighlightText(
    this.text, {
    super.key,
    this.style,
    this.maxLines,
    this.overflow,
    this.highlightColor,
  });

  final String text;
  final TextStyle? style;
  final int? maxLines;
  final TextOverflow? overflow;
  final Color? highlightColor;

  static final _markRe = RegExp(r'<mark>(.*?)</mark>', dotAll: true);

  @override
  Widget build(BuildContext context) {
    if (!text.contains('<mark>')) {
      return Text(text, style: style, maxLines: maxLines, overflow: overflow);
    }
    final highlight = highlightColor ??
        Theme.of(context).colorScheme.primary.withValues(alpha: 0.18);
    final spans = <TextSpan>[];
    var cursor = 0;
    for (final match in _markRe.allMatches(text)) {
      if (match.start > cursor) {
        spans.add(TextSpan(text: text.substring(cursor, match.start)));
      }
      spans.add(
        TextSpan(
          text: match.group(1) ?? '',
          style: TextStyle(backgroundColor: highlight, fontWeight: FontWeight.w600),
        ),
      );
      cursor = match.end;
    }
    if (cursor < text.length) {
      spans.add(TextSpan(text: text.substring(cursor)));
    }
    return Text.rich(
      TextSpan(style: style, children: spans),
      maxLines: maxLines,
      overflow: overflow,
    );
  }
}
