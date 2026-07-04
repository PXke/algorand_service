import 'dart:convert';
import 'dart:typed_data';

import 'package:algorand_auth_core/algorand_auth_core.dart';
import 'package:algorand_dart/algorand_dart.dart' as sdk;
import 'package:flutter_test/flutter_test.dart';

/// Ensures our minimal encoder matches algorand_dart for ARC-0025 auth txs.
void main() {
  test('auth payment bytes match algorand_dart', () async {
    const sender =
        'VKM6KSCTDHEM6KGEAMSYCNEGIPFJMHDSEMIRAQLK76CJDIRMMDHKAIRMFQ';
    const note = 'Sign me in';
    final gh = base64Decode('SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=');
    final params = SuggestedParams(
      feePerByte: 4,
      genesisId: 'testnet-v1.0',
      genesisHash: gh,
      lastRound: 42000000,
      minFee: 1000,
    );

    final ours = AuthPaymentTransaction.buildUnsignedBytes(
      senderAddress: sender,
      note: note,
      params: params,
      feeMicroAlgos: 0,
    );

    final sdkParams = sdk.TransactionParams(
      consensusVersion: 'future',
      fee: params.feePerByte,
      genesisId: params.genesisId,
      genesisHash: params.genesisHash,
      lastRound: params.lastRound,
      minFee: params.minFee,
    );
    final address = sdk.Address.fromAlgorandAddress(address: sender);
    final tx = await (sdk.PaymentTransactionBuilder()
          ..sender = address
          ..receiver = address
          ..amount = sdk.Algo.toMicroAlgos(0)
          ..noteText = note
          ..suggestedParams = sdkParams)
        .build();
    tx.fee = 0;
    final theirs = tx.toBytes();

    expect(ours, equals(theirs));
  });
}
