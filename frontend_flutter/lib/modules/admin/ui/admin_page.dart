import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/admin_provider.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/page_content.dart';
import 'admin_hub_page.dart';

/// Admin-only hub: sources, article editor, writer briefs.
class AdminPage extends ConsumerWidget {
  const AdminPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final isAdmin = ref.watch(isAdminWalletProvider);

    if (!isAdmin) {
      return PageContent(
        child: EmptyState(
          title: l10n.adminTitle,
          message: l10n.adminAccessDenied,
          icon: Icons.lock_outline,
        ),
      );
    }

    return const AdminHubPage();
  }
}
