import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:qr_flutter/qr_flutter.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import 'wallet_error_text.dart';

/// Shows WalletConnect pairing UI without blocking the WC session handshake.
///
/// When [authState] is provided the dialog reacts to the login flow: the QR
/// disappears once the wallet pairs (replaced by an "approve in your wallet"
/// step), a failure swaps in an error step with a retry button, and the dialog
/// closes itself when authentication completes. Any other dismissal — Cancel
/// or a barrier tap — reports [onCancel] so the pending WalletConnect attempt
/// is aborted instead of leaving the caller spinning.
void showWalletConnectUriDialog(
  BuildContext context,
  String uri, {
  VoidCallback? onCancel,
  VoidCallback? onRetry,
  ValueListenable<WalletAuthState>? authState,
}) {
  showDialog<String>(
    context: context,
    barrierDismissible: true,
    builder: (dialogContext) {
      return _WalletConnectUriDialog(uri: uri, authState: authState);
    },
  ).then((result) {
    // Retry is only reachable from the error step, where the failed attempt
    // has already been torn down — re-running the flow is all that's left.
    if (result == _WalletConnectUriDialog.resultRetry) {
      onRetry?.call();
      return;
    }
    if (result != _WalletConnectUriDialog.resultSuccess) {
      onCancel?.call();
    }
  });
}

class _WalletConnectUriDialog extends StatefulWidget {
  const _WalletConnectUriDialog({required this.uri, this.authState});

  static const resultSuccess = 'success';
  static const resultRetry = 'retry';

  final String uri;
  final ValueListenable<WalletAuthState>? authState;

  @override
  State<_WalletConnectUriDialog> createState() => _WalletConnectUriDialogState();
}

class _WalletConnectUriDialogState extends State<_WalletConnectUriDialog> {
  /// On phones the QR is useless (you cannot scan your own screen), so the
  /// deep link leads and the QR hides behind a toggle.
  static bool get _isMobile =>
      defaultTargetPlatform == TargetPlatform.android ||
      defaultTargetPlatform == TargetPlatform.iOS;

  bool _showQr = !_isMobile;

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
      Navigator.of(context).pop(_WalletConnectUriDialog.resultSuccess);
      return;
    }
    setState(() {});
  }

  Object? get _error => widget.authState?.value.error;

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

  Future<void> _launch(BuildContext context, Uri uri, {required String target}) async {
    final l10n = context.l10n;
    try {
      final launched = await launchUrl(
        uri,
        mode: LaunchMode.externalApplication,
        webOnlyWindowName: target,
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

  /// Bare `wc:topic@1` foregrounds the existing wallet session (standard
  /// WalletConnect v1 behavior) so the pending sign request becomes visible.
  Future<void> _reopenWallet(BuildContext context) =>
      _launch(context, Uri.parse(widget.uri.split('?').first), target: '_self');

  Future<void> _openWallet(BuildContext context) =>
      _launch(context, Uri.parse(widget.uri), target: _isMobile ? '_self' : '_blank');

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final error = _error;
    final paired = error == null && _isPaired;

    final IconData titleIcon;
    final String titleText;
    final Widget body;
    final List<Widget> actions;
    if (error != null) {
      titleIcon = Icons.error_outline;
      titleText = l10n.walletErrorTitle;
      body = _buildErrorStep(theme, colors, l10n, error);
      actions = [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(l10n.walletCancel),
        ),
        FilledButton.tonalIcon(
          onPressed: () =>
              Navigator.of(context).pop(_WalletConnectUriDialog.resultRetry),
          icon: const Icon(Icons.refresh, size: 20),
          label: Text(l10n.walletRetry),
        ),
      ];
    } else if (paired) {
      titleIcon = Icons.task_alt;
      titleText = l10n.walletAwaitingApprovalTitle;
      body = _buildApprovalStep(theme, colors, l10n);
      actions = [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(l10n.walletCancel),
        ),
        FilledButton.tonalIcon(
          onPressed: () => _reopenWallet(context),
          icon: const Icon(Icons.open_in_new, size: 20),
          label: Text(l10n.walletOpenWallet),
        ),
      ];
    } else {
      titleIcon = _isMobile ? Icons.account_balance_wallet_outlined : Icons.qr_code_2;
      titleText = l10n.walletDialogTitle;
      body = _buildConnectStep(theme, colors, l10n);
      actions = [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(l10n.walletCancel),
        ),
        if (!_isMobile)
          FilledButton.tonalIcon(
            onPressed: () => _openWallet(context),
            icon: const Icon(Icons.open_in_new, size: 20),
            label: Text(l10n.walletOpenWallet),
          ),
      ];
    }

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
            child: Icon(titleIcon, size: 20, color: theme.colorScheme.primary),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(titleText, style: theme.textTheme.titleMedium),
          ),
        ],
      ),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 320),
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: body,
        ),
      ),
      actions: actions,
    );
  }

  Widget _buildConnectStep(ThemeData theme, AppThemeColors colors, AppLocalizations l10n) {
    return SingleChildScrollView(
      key: const ValueKey('connect'),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: 12),
          Text(
            _isMobile ? l10n.walletMobileHint : l10n.walletDialogBody,
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
          if (_isMobile) ...[
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: () => _openWallet(context),
              icon: const Icon(Icons.account_balance_wallet_outlined, size: 20),
              label: Text(l10n.walletOpenWallet),
            ),
            const SizedBox(height: 8),
            TextButton.icon(
              onPressed: () => setState(() => _showQr = !_showQr),
              icon: Icon(
                _showQr ? Icons.expand_less : Icons.qr_code_2,
                size: 18,
              ),
              label: Text(l10n.walletShowQr),
            ),
          ],
          if (_showQr) ...[
            const SizedBox(height: 14),
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
          const SizedBox(height: 14),
          _SignExplainer(theme: theme, colors: colors, l10n: l10n),
        ],
      ),
    );
  }

  Widget _buildApprovalStep(ThemeData theme, AppThemeColors colors, AppLocalizations l10n) {
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
          const SizedBox(height: 16),
          _SignExplainer(theme: theme, colors: colors, l10n: l10n),
        ],
      ),
    );
  }

  Widget _buildErrorStep(
    ThemeData theme,
    AppThemeColors colors,
    AppLocalizations l10n,
    Object error,
  ) {
    return Padding(
      key: const ValueKey('error'),
      padding: const EdgeInsets.only(top: 20, bottom: 8),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.link_off, size: 36, color: theme.colorScheme.error),
          const SizedBox(height: 16),
          Text(
            walletErrorText(l10n, error),
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium,
          ),
        ],
      ),
    );
  }
}

/// "The wallet will show a 0-ALGO transaction" note — the signing prompt looks
/// like a payment inside Pera, so say up front that it is free and never sent.
class _SignExplainer extends StatelessWidget {
  const _SignExplainer({
    required this.theme,
    required this.colors,
    required this.l10n,
  });

  final ThemeData theme;
  final AppThemeColors colors;
  final AppLocalizations l10n;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.info_outline, size: 16, color: colors.subtle),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            l10n.walletSignExplainer,
            style: theme.textTheme.bodySmall?.copyWith(
              color: colors.subtle,
              fontSize: 11.5,
              height: 1.4,
            ),
          ),
        ),
      ],
    );
  }
}
