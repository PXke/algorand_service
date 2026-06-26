import 'package:flutter/material.dart';

import '../../../l10n/app_localizations.dart';

/// Mirrors backend `source_kind` from scrape_url classification.
enum SourceKind {
  discord,
  reddit,
  web,
  chainOnly,
  unknown;

  static SourceKind fromApi(String? value) {
    switch (value) {
      case 'discord':
        return SourceKind.discord;
      case 'reddit':
        return SourceKind.reddit;
      case 'web':
        return SourceKind.web;
      case 'chain_only':
        return SourceKind.chainOnly;
      default:
        return SourceKind.unknown;
    }
  }

  String label(AppLocalizations l10n) {
    switch (this) {
      case SourceKind.discord:
        return l10n.sourceKindDiscord;
      case SourceKind.reddit:
        return l10n.sourceKindReddit;
      case SourceKind.web:
        return l10n.sourceKindWeb;
      case SourceKind.chainOnly:
        return l10n.sourceKindOnChain;
      case SourceKind.unknown:
        return l10n.sourceKindUnknown;
    }
  }

  IconData get icon {
    switch (this) {
      case SourceKind.discord:
        return Icons.forum_outlined;
      case SourceKind.reddit:
        return Icons.groups_outlined;
      case SourceKind.web:
        return Icons.language_outlined;
      case SourceKind.chainOnly:
        return Icons.link_outlined;
      case SourceKind.unknown:
        return Icons.rss_feed_outlined;
    }
  }

  Color background(ColorScheme scheme) {
    final isDark = scheme.brightness == Brightness.dark;
    switch (this) {
      case SourceKind.discord:
        return isDark ? const Color(0xFF1E2A3D) : const Color(0xFFE8EEF8);
      case SourceKind.reddit:
        return isDark ? const Color(0xFF2E221E) : const Color(0xFFF5EBE8);
      case SourceKind.web:
        return isDark ? const Color(0xFF1E2E28) : const Color(0xFFE8F0EC);
      case SourceKind.chainOnly:
        return scheme.surfaceContainerHighest;
      case SourceKind.unknown:
        return scheme.surfaceContainerHigh;
    }
  }

  Color foreground(ColorScheme scheme) {
    final isDark = scheme.brightness == Brightness.dark;
    switch (this) {
      case SourceKind.discord:
        return isDark ? const Color(0xFF8EB4F0) : const Color(0xFF1A3A6B);
      case SourceKind.reddit:
        return isDark ? const Color(0xFFE8A88A) : const Color(0xFF6B3A1A);
      case SourceKind.web:
        return isDark ? const Color(0xFF8AD4B8) : const Color(0xFF1A4D3A);
      case SourceKind.chainOnly:
        return scheme.onSurfaceVariant;
      case SourceKind.unknown:
        return scheme.onSurfaceVariant;
    }
  }
}

String? sourceKindForScrapeUrl(String? scrapeUrl) {
  if (scrapeUrl == null || scrapeUrl.trim().isEmpty) {
    return 'chain_only';
  }
  final raw = scrapeUrl.trim().toLowerCase();
  if (raw.startsWith('discord:')) {
    return 'discord';
  }
  if (raw.startsWith('reddit:')) {
    return 'reddit';
  }
  return 'web';
}
