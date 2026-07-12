import 'package:flutter/widgets.dart';

import '../l10n/l10n_extensions.dart';

/// Human-readable formatting for feed metadata.
String formatEpoch(int? epoch) {
  if (epoch == null) {
    return '';
  }
  final dt = DateTime.fromMillisecondsSinceEpoch(epoch * 1000, isUtc: true).toLocal();
  final y = dt.year;
  final m = dt.month.toString().padLeft(2, '0');
  final d = dt.day.toString().padLeft(2, '0');
  final h = dt.hour.toString().padLeft(2, '0');
  final min = dt.minute.toString().padLeft(2, '0');
  return '$y-$m-$d $h:$min';
}

/// Relative time label (e.g. "5m ago") with absolute fallback for older dates.
String formatRelativeEpoch(BuildContext context, int? epoch) {
  if (epoch == null) {
    return '';
  }
  final l10n = context.l10n;
  final dt = DateTime.fromMillisecondsSinceEpoch(epoch * 1000, isUtc: true).toLocal();
  final now = DateTime.now();
  final diff = now.difference(dt);
  if (diff.inSeconds < 60) {
    return l10n.timeJustNow;
  }
  if (diff.inMinutes < 60) {
    return l10n.timeMinutesAgo(diff.inMinutes);
  }
  if (diff.inHours < 24) {
    return l10n.timeHoursAgo(diff.inHours);
  }
  if (diff.inDays < 14) {
    return l10n.timeDaysAgo(diff.inDays);
  }
  return formatEpoch(epoch);
}

/// Strips common Markdown syntax so summaries/decks render as clean plain text
/// in cards and standfirsts (the body still renders full Markdown).
String stripMarkdown(String value) {
  var out = value;
  // Links: [text](url) -> text
  out = out.replaceAllMapped(RegExp(r'\[([^\]]+)\]\([^)]*\)'), (m) => m[1] ?? '');
  // Images: ![alt](url) -> alt
  out = out.replaceAllMapped(RegExp(r'!\[([^\]]*)\]\([^)]*\)'), (m) => m[1] ?? '');
  // Emphasis / code markers and leading heading/quote markers.
  out = out.replaceAll(RegExp(r'(\*\*|\*|__|_|`|~~)'), '');
  out = out.replaceAll(RegExp(r'^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+)', multiLine: true), '');
  return out.replaceAll(RegExp(r'\s+'), ' ').trim();
}

String truncateMiddle(String value, {int head = 8, int tail = 6}) {
  if (value.length <= head + tail + 3) {
    return value;
  }
  return '${value.substring(0, head)}…${value.substring(value.length - tail)}';
}

/// Turns raw pipeline service identifiers into reader-facing labels.
///
/// The feed stores whatever the pipeline used as its work-unit key: a slug
/// (`downbad-farm`), a URL (`https://kryptonurd.com/`), a forum topic
/// (`forum-topic:15198`) or an internal editorial-brief UUID. Only the first
/// two mean anything to a reader; internal keys must never print as-is.
String? humanizeServiceId(String? serviceId) {
  final raw = serviceId?.trim();
  if (raw == null || raw.isEmpty) return null;
  if (raw.startsWith('editorial-brief:')) return null;
  if (raw.startsWith('forum-topic:')) return 'Algorand Forum';
  if (raw.startsWith('http://') || raw.startsWith('https://')) {
    final host = Uri.tryParse(raw)?.host ?? '';
    if (host.isEmpty) return null;
    return host.startsWith('www.') ? host.substring(4) : host;
  }
  // Internal keys that slipped through (uuid-ish tails) stay hidden.
  if (RegExp(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
      .hasMatch(raw)) {
    return null;
  }
  return raw;
}

String formatArticleMetaLine(
  BuildContext context, {
  int? publishedEpoch,
  Object? round,
  String? serviceId,
  bool relativeTime = true,
}) {
  final l10n = context.l10n;
  final publishedLabel = publishedEpoch == null
      ? null
      : relativeTime
          ? l10n.metaPublishedRelative(formatRelativeEpoch(context, publishedEpoch))
          : l10n.metaPublishedEpoch(formatEpoch(publishedEpoch));
  final service = humanizeServiceId(serviceId);
  final serviceLabel = service == null ? null : l10n.metaService(service);
  final parts = <String>[
    ?publishedLabel,
    if (round != null) l10n.metaRound(round.toString()),
    ?serviceLabel,
  ];
  return parts.join('  ·  ');
}
