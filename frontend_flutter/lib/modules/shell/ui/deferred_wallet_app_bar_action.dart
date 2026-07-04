import 'package:flutter/material.dart';

import '../../../shared/widgets/deferred_widget.dart';
import '../../auth/auth_entry.dart' deferred as auth;

/// Wallet connect control that loads the auth chunk on demand.
class DeferredWalletAppBarAction extends StatelessWidget {
  const DeferredWalletAppBarAction({super.key});

  @override
  Widget build(BuildContext context) {
    return DeferredWidget(
      auth.loadLibrary,
      () => auth.WalletAppBarAction(),
      placeholder: IconButton(
        tooltip: 'Wallet',
        onPressed: null,
        icon: Icon(
          Icons.account_balance_wallet_outlined,
          size: 20,
          color: Theme.of(context).disabledColor,
        ),
      ),
    );
  }
}
