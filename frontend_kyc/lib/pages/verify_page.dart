import 'package:flutter/material.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../config.dart';
import '../services/x402_client.dart';

/// Demo of the actual paid product: pay to check a wallet's KYC status. In
/// the real product this is called server-to-server by a third party (an
/// exchange, a faucet); this page exists so a real payment can be made
/// end-to-end from the browser — which is also how the required first real
/// MainNet settlement gets produced if no external integrator moves first.
class VerifyPage extends StatefulWidget {
  const VerifyPage({super.key, required this.connector, required this.walletAddress});

  final WalletConnectAlgorandConnector connector;
  final String? walletAddress;

  @override
  State<VerifyPage> createState() => _VerifyPageState();
}

class _VerifyPageState extends State<VerifyPage> {
  final _walletController = TextEditingController();
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _result;

  @override
  void dispose() {
    _walletController.dispose();
    super.dispose();
  }

  Future<void> _verify() async {
    final payer = widget.walletAddress;
    final target = _walletController.text.trim();
    if (payer == null || target.isEmpty) return;

    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final client = X402Client(baseUrl: AppConfig.instance.apiBaseUrl, connector: widget.connector);
      final result = await client.getPaid('/api/v1/kyc/verify?wallet=$target', payerAddress: payer);
      setState(() {
        _result = result;
        _busy = false;
      });
    } catch (e) {
      setState(() {
        _error = e is X402FeeAbstractionNotSupported ? e.message : e.toString();
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final payer = widget.walletAddress;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Pay to check a wallet', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 8),
              const Text(
                'Enter any Algorand wallet address. Paying charges your connected wallet '
                'via x402 — if the wallet is enrolled, half the fee goes straight to it.',
              ),
              const SizedBox(height: 24),
              if (payer == null)
                const Text('Connect a wallet above to pay for a lookup.')
              else ...[
                TextField(
                  controller: _walletController,
                  decoration: const InputDecoration(
                    labelText: 'Wallet address to check',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _busy ? null : _verify,
                  child: Text(_busy ? 'Paying & checking…' : 'Pay & verify'),
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
                        Text(
                          _result!['enrolled'] == true ? 'Enrolled' : 'Not enrolled',
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                        if (_result!['enrolled'] == true) ...[
                          const SizedBox(height: 8),
                          Text('KYC level: ${_result!['kyc_level']}'),
                          Text('Payout status: ${_result!['payout_status']}'),
                        ],
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
