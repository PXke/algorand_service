import 'package:flutter/material.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../services/kyc_api.dart';

class EnrollPage extends StatefulWidget {
  const EnrollPage({super.key, required this.connector, required this.walletAddress});

  final WalletConnectAlgorandConnector connector;
  final String? walletAddress;

  @override
  State<EnrollPage> createState() => _EnrollPageState();
}

class _EnrollPageState extends State<EnrollPage> {
  final _api = KycApi();
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _result;

  Future<void> _enroll() async {
    final wallet = widget.walletAddress;
    if (wallet == null) return;
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final message = await _api.fetchConsentMessage(wallet);
      final signature = await widget.connector.signArbitraryData(
        walletAddress: wallet,
        message: message,
        prompt: 'Enroll this wallet in Algorand KYC',
      );
      if (signature == null) {
        throw KycApiError('Your wallet did not return a signature for the consent message.');
      }
      final result = await _api.enroll(
        walletAddress: wallet,
        consentSignatureB64: signature,
      );
      setState(() {
        _result = result;
        _busy = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final wallet = widget.walletAddress;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Enroll your wallet', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 8),
              const Text(
                'Free, one-time. We compute trust signals for your wallet from public '
                'chain data (age, recent activity) and store a KYC level. From then on, '
                'anyone who pays to look you up sends half the fee straight to this wallet.',
              ),
              const SizedBox(height: 24),
              if (wallet == null)
                const Text('Connect a wallet above to enroll.')
              else ...[
                Text('Wallet: $wallet', style: Theme.of(context).textTheme.bodySmall),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _busy ? null : _enroll,
                  child: Text(_busy ? 'Enrolling…' : 'Sign consent & enroll'),
                ),
              ],
              if (_error != null) ...[
                const SizedBox(height: 16),
                Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ],
              if (_result != null) ...[
                const SizedBox(height: 16),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Enrolled', style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: 8),
                        Text('KYC level: ${_result!['kyc_level']}'),
                        Text('Wallet age (round): ${_result!['wallet_age_round'] ?? 'unknown'}'),
                        Text('Recent transactions: ${_result!['recent_tx_count']}'),
                      ],
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
