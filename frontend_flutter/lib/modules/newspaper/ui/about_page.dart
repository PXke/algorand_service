import 'package:flutter/material.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/footer_scaffold.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/page_content.dart';

/// About / masthead page: who we are, how we publish, and our standards —
/// the trust page a serious publication is expected to have.
class AboutPage extends StatelessWidget {
  const AboutPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;

    return FooterScaffold(
      content: Padding(
        padding: responsivePagePadding(context),
        child: PageContent(
          child: Align(
            alignment: Alignment.topCenter,
            child: ConstrainedBox(
              constraints:
                  const BoxConstraints(maxWidth: AppLayout.maxReadingWidth),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                    // Same header anatomy as every other page: accent slug
                    // under a serif title, then the lead paragraph.
                    Container(width: 34, height: 3, color: colors.accent),
                    const SizedBox(height: 14),
                    Text(
                      l10n.aboutTitle,
                      style: theme.textTheme.headlineLarge?.copyWith(fontSize: 34),
                    ),
                    const SizedBox(height: 18),
                    Text(
                      l10n.aboutLead,
                      style: theme.textTheme.bodyLarge?.copyWith(
                        fontSize: 19,
                        height: 1.6,
                        color: colors.muted,
                      ),
                    ),
                    const SizedBox(height: AppLayout.sectionGap),
                    const Divider(),
                    _Block(
                      heading: l10n.aboutHowHeading,
                      body: l10n.aboutHowBody,
                    ),
                    _Block(
                      heading: l10n.aboutAiHeading,
                      body: l10n.aboutAiBody,
                    ),
                    _Block(
                      heading: l10n.aboutProvenanceHeading,
                      body: l10n.aboutProvenanceBody,
                    ),
                    _Block(
                      heading: l10n.aboutStandardsHeading,
                      body: l10n.aboutStandardsBody,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
    );
  }
}

class _Block extends StatelessWidget {
  const _Block({required this.heading, required this.body});

  final String heading;
  final String body;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Padding(
      padding: const EdgeInsets.only(top: AppLayout.sectionGap),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(heading, style: theme.textTheme.titleLarge),
          const SizedBox(height: 10),
          Text(
            body,
            style: theme.textTheme.bodyLarge?.copyWith(height: 1.6),
          ),
        ],
      ),
    );
  }
}
