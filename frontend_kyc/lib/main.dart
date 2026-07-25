import 'package:flutter/material.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import 'config.dart';
import 'pages/enroll_page.dart';
import 'pages/verify_page.dart';

void main() {
  runApp(const KycApp());
}

class KycApp extends StatelessWidget {
  const KycApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Algorand KYC',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        useMaterial3: true,
      ),
      home: const HomeShell(),
    );
  }
}

/// Owns the single WalletConnect session shared by both pages (enroll and
/// verify-demo both need a connected wallet, but the demo lookup pays FOR a
/// different wallet than it necessarily signs with — connecting once here
/// keeps that distinction visible instead of hiding two separate connects).
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with SingleTickerProviderStateMixin {
  late final WalletConnectAlgorandConnector _connector;
  late final TabController _tabController;
  String? _walletAddress;
  bool _connecting = false;
  String? _connectError;
  String? _wcUri;

  @override
  void initState() {
    super.initState();
    final config = AppConfig.instance;
    _connector = WalletConnectAlgorandConnector(
      config: WalletAuthConfig(
        apiBaseUrl: config.apiBaseUrl,
        algodApiUrl: config.algodApiUrl,
        walletConnectBridge: config.walletConnectBridge,
        walletConnectChainId: config.walletConnectChainId,
        dappName: 'Algorand KYC',
        dappDescription: 'Wallet-linked KYC status for the Algorand ecosystem',
      ),
    );
    _tabController = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    setState(() {
      _connecting = true;
      _connectError = null;
      _wcUri = null;
    });
    try {
      final connection = await _connector.connect(
        onDisplayUri: (uri) => setState(() => _wcUri = uri),
      );
      setState(() {
        _walletAddress = connection.walletAddress;
        _connecting = false;
        _wcUri = null;
      });
    } catch (e) {
      setState(() {
        _connectError = e.toString();
        _connecting = false;
        _wcUri = null;
      });
    }
  }

  Future<void> _disconnect() async {
    await _connector.disconnect();
    setState(() => _walletAddress = null);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Algorand KYC'),
        bottom: TabBar(
          controller: _tabController,
          tabs: const [
            Tab(text: 'Enroll'),
            Tab(text: 'Verify (demo)'),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Center(child: _walletChip()),
          ),
        ],
      ),
      body: Column(
        children: [
          if (_wcUri != null) _wcUriBanner(),
          if (_connectError != null) _errorBanner(_connectError!),
          Expanded(
            child: TabBarView(
              controller: _tabController,
              children: [
                EnrollPage(connector: _connector, walletAddress: _walletAddress),
                VerifyPage(connector: _connector, walletAddress: _walletAddress),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _walletChip() {
    if (_walletAddress != null) {
      final addr = _walletAddress!;
      final short = '${addr.substring(0, 6)}…${addr.substring(addr.length - 4)}';
      return TextButton.icon(
        onPressed: _disconnect,
        icon: const Icon(Icons.account_balance_wallet, size: 18),
        label: Text(short),
      );
    }
    return FilledButton(
      onPressed: _connecting ? null : _connect,
      child: Text(_connecting ? 'Connecting…' : 'Connect wallet'),
    );
  }

  Widget _wcUriBanner() {
    return MaterialBanner(
      content:
          SelectableText('Open your Algorand wallet app and approve the connection.\n$_wcUri'),
      actions: [
        TextButton(onPressed: () => setState(() => _wcUri = null), child: const Text('Dismiss')),
      ],
    );
  }

  Widget _errorBanner(String message) {
    return MaterialBanner(
      backgroundColor: Theme.of(context).colorScheme.errorContainer,
      content: Text(message),
      actions: [
        TextButton(
          onPressed: () => setState(() => _connectError = null),
          child: const Text('Dismiss'),
        ),
      ],
    );
  }
}
