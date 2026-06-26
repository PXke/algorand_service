import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/config/app_config.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
class FeedPlacementCard extends StatelessWidget {
  const FeedPlacementCard({super.key, required this.placement});

  final Map<String, dynamic> placement;

  String? get _targetUrl {
    final raw = placement['target_url']?.toString() ?? '';
    return raw.isEmpty ? null : raw;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final sponsor = placement['sponsor_name']?.toString() ?? '';
    final headline = placement['headline']?.toString() ?? '';
    final body = placement['body']?.toString() ?? '';
    final imageUrl = placement['image_url']?.toString();

    return Material(
      elevation: 0,
      color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.35),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: colors.border),
      ),
      child: InkWell(
        onTap: _targetUrl == null ? null : () => _openUrl(_targetUrl!),
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: const Color(0xFFD97706).withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(
                        color: const Color(0xFFD97706).withValues(alpha: 0.4),
                      ),
                    ),
                    child: Text(
                      l10n.newsSponsoredLabel.toUpperCase(),
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: const Color(0xFFB45309),
                        letterSpacing: 1.0,
                        fontWeight: FontWeight.w800,
                        fontSize: 10,
                      ),
                    ),
                  ),
                  if (sponsor.isNotEmpty) ...[
                    const SizedBox(width: 8),
                    Flexible(
                      child: Text(
                        sponsor,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: colors.muted,
                        ),
                      ),
                    ),
                  ],
                ],
              ),
              if (headline.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text(
                  headline,
                  style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                ),
              ],
              if (imageUrl != null && imageUrl.isNotEmpty) ...[
                const SizedBox(height: 12),
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Image.network(
                    proxiedImageUrl(imageUrl),
                    height: 120,
                    width: double.infinity,
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => const SizedBox.shrink(),
                  ),
                ),
              ],
              if (body.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text(
                  body,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium?.copyWith(height: 1.5),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _openUrl(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }
}
