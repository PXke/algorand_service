import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../providers/auth_providers.dart';
import 'wallet_connect_dialog.dart';
import 'wallet_error_text.dart';

class WalletAuthPanel extends ConsumerWidget {
  const WalletAuthPanel({super.key});

  Future<void> _startSignIn(BuildContext context, WalletAuthClient client) {
    return client.connectAndSignIn(
      onDisplayUri: (uri) {
        showWalletConnectUriDialog(
          context,
          uri,
          onCancel: client.cancelPendingConnect,
          onRetry: () => _startSignIn(context, client),
          onResumed: client.wakeTransport,
          authState: client.state,
        );
      },
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final client = ref.watch(walletAuthClientProvider);
    final theme = Theme.of(context);
    final l10n = context.l10n;

    return ValueListenableBuilder<WalletAuthState>(
      valueListenable: client.state,
      builder: (context, auth, _) {
        if (auth.isAuthenticated) {
          return _Panel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    Icon(Icons.account_balance_wallet_outlined, size: 18, color: theme.colorScheme.primary),
                    const SizedBox(width: 8),
                    Text(l10n.walletConnected, style: theme.textTheme.titleSmall),
                  ],
                ),
                const SizedBox(height: 12),
                SelectableText(
                  auth.walletAddress ?? '',
                  style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
                ),
                const SizedBox(height: 16),
                OutlinedButton(
                  onPressed: auth.isLoading ? null : client.logout,
                  child: Text(l10n.walletDisconnect),
                ),
              ],
            ),
          );
        }

        final error = auth.error;

        return _Panel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l10n.walletSignInTitle, style: theme.textTheme.titleSmall),
              const SizedBox(height: 8),
              Text(l10n.walletSignInBody, style: theme.textTheme.bodySmall),
              if (error != null) ...[
                const SizedBox(height: 12),
                Text(
                  walletErrorText(l10n, error),
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.error,
                  ),
                ),
              ],
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: auth.isLoading ? null : () => _startSignIn(context, client),
                icon: auth.isLoading
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.qr_code_2, size: 18),
                label: Text(l10n.walletConnect),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final cardColor = Theme.of(context).cardTheme.color;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: cardColor,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      child: child,
    );
  }
}
