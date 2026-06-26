import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:qr_flutter/qr_flutter.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';

/// Shows WalletConnect pairing UI without blocking the WC session handshake.
///
/// When [authState] is provided the dialog reacts to the login flow: the QR
/// disappears once the wallet pairs (replaced by an "approve in your wallet"
/// step) and the dialog closes itself when authentication completes.
void showWalletConnectUriDialog(
  BuildContext context,
  String uri, {
  VoidCallback? onCancel,
  ValueListenable<WalletAuthState>? authState,
}) {
  showDialog<void>(
    context: context,
    barrierDismissible: true,
    builder: (dialogContext) {
      return _WalletConnectUriDialog(
        uri: uri,
        onCancel: onCancel,
        authState: authState,
      );
    },
  );
}

class _WalletConnectUriDialog extends StatefulWidget {
  const _WalletConnectUriDialog({
    required this.uri,
    this.onCancel,
    this.authState,
  });

  final String uri;
  final VoidCallback? onCancel;
  final ValueListenable<WalletAuthState>? authState;

  @override
  State<_WalletConnectUriDialog> createState() => _WalletConnectUriDialogState();
}

class _WalletConnectUriDialogState extends State<_WalletConnectUriDialog> {
  @override
  void initState() {
    super.initState();
    widget.authState?.addListener(_onAuthChanged);
  }

  @override
  void dispose() {
    widget.authState?.removeListener(_onAuthChanged);
    super.dispose();
  }

  void _onAuthChanged() {
    final auth = widget.authState!.value;
    if (!mounted) return;
    if (auth.isAuthenticated) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {});
  }

  bool get _isPaired {
    final address = widget.authState?.value.walletAddress;
    return address != null && address.isNotEmpty;
  }

  Future<void> _copyUri(BuildContext context) async {
    final l10n = context.l10n;
    await Clipboard.setData(ClipboardData(text: widget.uri));
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(l10n.walletUriCopied)),
    );
  }

  Future<void> _reopenWallet(BuildContext context) async {
    // Bare `wc:topic@1` foregrounds the existing wallet session (standard
    // WalletConnect v1 behavior) so the pending sign request becomes visible.
    final bare = widget.uri.split('?').first;
    final l10n = context.l10n;
    try {
      final launched = await launchUrl(
        Uri.parse(bare),
        mode: LaunchMode.externalApplication,
        webOnlyWindowName: '_self',
      );
      if (!launched && context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.walletOpenFailed)),
        );
      }
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.walletOpenFailed)),
        );
      }
    }
  }

  Future<void> _openWallet(BuildContext context) async {
    final l10n = context.l10n;
    final launchUri = Uri.parse(widget.uri);
    try {
      final launched = await launchUrl(
        launchUri,
        mode: LaunchMode.externalApplication,
        webOnlyWindowName: '_blank',
      );
      if (!launched && context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.walletOpenFailed)),
        );
      }
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.walletOpenFailed)),
        );
      }
    }
  }

  void _close(BuildContext context, {bool cancelled = false}) {
    if (cancelled) {
      widget.onCancel?.call();
    }
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final paired = _isPaired;

    return AlertDialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      titlePadding: const EdgeInsets.fromLTRB(24, 24, 24, 0),
      title: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: theme.colorScheme.primary.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(
              paired ? Icons.task_alt : Icons.qr_code_2,
              size: 20,
              color: theme.colorScheme.primary,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              paired ? l10n.walletAwaitingApprovalTitle : l10n.walletDialogTitle,
              style: theme.textTheme.titleMedium,
            ),
          ),
        ],
      ),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 320),
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: paired ? _buildApprovalStep(theme, l10n) : _buildQrStep(theme, colors, l10n),
        ),
      ),
      actions: paired
          ? [
              TextButton(
                onPressed: () => _close(context, cancelled: true),
                child: Text(l10n.walletCancel),
              ),
              FilledButton.tonalIcon(
                onPressed: () => _reopenWallet(context),
                icon: const Icon(Icons.open_in_new, size: 20),
                label: Text(l10n.walletOpenWallet),
              ),
            ]
          : [
              TextButton(
                onPressed: () => _close(context, cancelled: true),
                child: Text(l10n.walletCancel),
              ),
              FilledButton.tonalIcon(
                onPressed: () => _openWallet(context),
                icon: const Icon(Icons.open_in_new, size: 20),
                label: Text(l10n.walletOpenWallet),
              ),
            ],
    );
  }

  Widget _buildQrStep(ThemeData theme, AppThemeColors colors, AppLocalizations l10n) {
    return SingleChildScrollView(
      key: const ValueKey('qr'),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: 12),
          Text(
            l10n.walletDialogBody,
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
          const SizedBox(height: 16),
          Center(
            child: Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: colors.border),
              ),
              child: SizedBox(
                width: 208,
                height: 208,
                child: CustomPaint(
                  painter: QrPainter(
                    data: widget.uri,
                    version: QrVersions.auto,
                    gapless: true,
                    errorCorrectionLevel: QrErrorCorrectLevel.M,
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(height: 14),
          Container(
            padding: const EdgeInsets.only(left: 12, right: 4),
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.4),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    widget.uri,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall?.copyWith(
                      fontFamily: 'monospace',
                      fontSize: 11,
                      color: colors.muted,
                    ),
                  ),
                ),
                IconButton(
                  tooltip: l10n.walletCopyUri,
                  iconSize: 20,
                  visualDensity: VisualDensity.compact,
                  onPressed: () => _copyUri(context),
                  icon: const Icon(Icons.copy_outlined),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildApprovalStep(ThemeData theme, AppLocalizations l10n) {
    final address = widget.authState?.value.walletAddress ?? '';
    final shortAddress = address.length > 12
        ? '${address.substring(0, 6)}…${address.substring(address.length - 6)}'
        : address;

    return Padding(
      key: const ValueKey('approval'),
      padding: const EdgeInsets.only(top: 20, bottom: 8),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(
            width: 36,
            height: 36,
            child: CircularProgressIndicator(strokeWidth: 3),
          ),
          const SizedBox(height: 20),
          Text(
            l10n.walletAwaitingApproval,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: 14),
          Chip(
            avatar: Icon(
              Icons.account_balance_wallet_outlined,
              size: 20,
              color: theme.colorScheme.primary,
            ),
            label: Text(
              shortAddress,
              style: theme.textTheme.labelMedium?.copyWith(fontFamily: 'monospace'),
            ),
            visualDensity: VisualDensity.compact,
          ),
        ],
      ),
    );
  }
}
