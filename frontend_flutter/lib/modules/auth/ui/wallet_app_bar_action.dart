import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../providers/auth_providers.dart';
import 'wallet_connect_dialog.dart';

/// Compact wallet control for the top app bar.
class WalletAppBarAction extends ConsumerWidget {
  const WalletAppBarAction({super.key});

  static String _shortAddress(String address) {
    if (address.length <= 12) return address;
    return '${address.substring(0, 4)}…${address.substring(address.length - 4)}';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final client = ref.watch(walletAuthClientProvider);
    final l10n = context.l10n;
    final theme = Theme.of(context);

    return ValueListenableBuilder<WalletAuthState>(
      valueListenable: client.state,
      builder: (context, auth, _) {
        if (auth.isAuthenticated) {
          final address = auth.walletAddress ?? '';
          final narrowToolbar = MediaQuery.sizeOf(context).width < 720;
          if (narrowToolbar) {
            return Padding(
              padding: const EdgeInsets.only(right: 4),
              child: PopupMenuButton<String>(
                tooltip: l10n.walletConnected,
                offset: const Offset(0, 40),
                icon: Icon(
                  Icons.account_balance_wallet,
                  size: 22,
                  color: theme.colorScheme.primary,
                ),
                onSelected: (value) {
                  if (value == 'disconnect') {
                    client.logout();
                  }
                },
                itemBuilder: (context) => [
                  PopupMenuItem(
                    enabled: false,
                    child: SelectableText(
                      address,
                      style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
                    ),
                  ),
                  const PopupMenuDivider(),
                  PopupMenuItem(
                    value: 'disconnect',
                    child: Text(l10n.walletDisconnect),
                  ),
                ],
              ),
            );
          }

          return Padding(
            padding: const EdgeInsets.only(right: 4),
            child: PopupMenuButton<String>(
              tooltip: l10n.walletConnected,
              offset: const Offset(0, 40),
              child: Chip(
                avatar: Icon(
                  Icons.account_balance_wallet_outlined,
                  size: 20,
                  color: theme.colorScheme.primary,
                ),
                label: Text(
                  _shortAddress(address),
                  style: theme.textTheme.labelMedium,
                ),
                padding: const EdgeInsets.symmetric(horizontal: 4),
                visualDensity: VisualDensity.compact,
              ),
              onSelected: (value) {
                if (value == 'disconnect') {
                  client.logout();
                }
              },
              itemBuilder: (context) => [
                PopupMenuItem(
                  enabled: false,
                  child: SelectableText(
                    address,
                    style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
                  ),
                ),
                const PopupMenuDivider(),
                PopupMenuItem(
                  value: 'disconnect',
                  child: Text(l10n.walletDisconnect),
                ),
              ],
            ),
          );
        }

        final compact = MediaQuery.sizeOf(context).width < 520;
        if (compact) {
          return IconButton(
            tooltip: l10n.walletConnect,
            onPressed: auth.isLoading
                ? null
                : () async {
                    await client.connectAndSignIn(
                      onDisplayUri: (uri) {
                        showWalletConnectUriDialog(
                          context,
                          uri,
                          onCancel: client.cancelPendingConnect,
                          authState: client.state,
                        );
                      },
                    );
                  },
            icon: auth.isLoading
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.account_balance_wallet_outlined, size: 20),
          );
        }
        return Padding(
          padding: const EdgeInsets.only(right: 8),
          child: FilledButton.tonalIcon(
            onPressed: auth.isLoading
                ? null
                : () async {
                    await client.connectAndSignIn(
                      onDisplayUri: (uri) {
                        showWalletConnectUriDialog(
                          context,
                          uri,
                          onCancel: client.cancelPendingConnect,
                          authState: client.state,
                        );
                      },
                    );
                  },
            icon: auth.isLoading
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.account_balance_wallet_outlined, size: 18),
            label: Text(l10n.walletConnect),
          ),
        );
      },
    );
  }
}
