import 'dart:convert';

import 'package:algorand_auth_core/algorand_auth_core.dart';
import 'package:algorand_dart/algorand_dart.dart' as sdk;
import 'package:flutter_test/flutter_test.dart';

/// Ensures our minimal encoder matches algorand_dart for ASA-transfer
/// transactions — this one carries a real fee and gets actually submitted
/// (unlike the auth payment tx), so a byte mismatch here would mean a real
/// x402 payment gets rejected or, worse, silently pays the wrong amount.
void main() {
  test('asset transfer bytes match algorand_dart', () async {
    const sender = 'VKM6KSCTDHEM6KGEAMSYCNEGIPFJMHDSEMIRAQLK76CJDIRMMDHKAIRMFQ';
    const receiver = 'DGRIWJLEI4CCV25TTYA4Y2PWZ2HEWLN6QFXH32FSMCU36RZKFCD6WM52QM';
    const assetId = 31566704; // USDC mainnet ASA
    const amount = 500000; // 0.5 USDC (6 decimals)
    final gh = base64Decode('SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=');
    final params = SuggestedParams(
      feePerByte: 4,
      genesisId: 'testnet-v1.0',
      genesisHash: gh,
      lastRound: 42000000,
      minFee: 1000,
    );

    final ours = AssetTransferTransaction.buildUnsignedBytes(
      senderAddress: sender,
      receiverAddress: receiver,
      assetId: assetId,
      amount: amount,
      params: params,
      feeMicroAlgos: params.minFee,
    );

    final sdkParams = sdk.TransactionParams(
      consensusVersion: 'future',
      fee: params.feePerByte,
      genesisId: params.genesisId,
      genesisHash: params.genesisHash,
      lastRound: params.lastRound,
      minFee: params.minFee,
    );
    final tx = await (sdk.AssetTransferTransactionBuilder()
          ..sender = sdk.Address.fromAlgorandAddress(address: sender)
          ..receiver = sdk.Address.fromAlgorandAddress(address: receiver)
          ..assetId = assetId
          ..amount = amount
          ..suggestedParams = sdkParams)
        .build();
    tx.fee = params.minFee;
    final theirs = tx.toBytes();

    expect(ours, equals(theirs));
  });

  test('asset transfer bytes match algorand_dart with a note', () async {
    const sender = 'VKM6KSCTDHEM6KGEAMSYCNEGIPFJMHDSEMIRAQLK76CJDIRMMDHKAIRMFQ';
    const receiver = 'DGRIWJLEI4CCV25TTYA4Y2PWZ2HEWLN6QFXH32FSMCU36RZKFCD6WM52QM';
    const assetId = 10458941; // USDC testnet ASA
    const amount = 25000;
    const note = 'x402 kyc-verify';
    final gh = base64Decode('SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=');
    final params = SuggestedParams(
      feePerByte: 4,
      genesisId: 'testnet-v1.0',
      genesisHash: gh,
      lastRound: 42000000,
      minFee: 1000,
    );

    final ours = AssetTransferTransaction.buildUnsignedBytes(
      senderAddress: sender,
      receiverAddress: receiver,
      assetId: assetId,
      amount: amount,
      params: params,
      note: note,
      feeMicroAlgos: params.minFee,
    );

    final sdkParams = sdk.TransactionParams(
      consensusVersion: 'future',
      fee: params.feePerByte,
      genesisId: params.genesisId,
      genesisHash: params.genesisHash,
      lastRound: params.lastRound,
      minFee: params.minFee,
    );
    final tx = await (sdk.AssetTransferTransactionBuilder()
          ..sender = sdk.Address.fromAlgorandAddress(address: sender)
          ..receiver = sdk.Address.fromAlgorandAddress(address: receiver)
          ..assetId = assetId
          ..amount = amount
          ..noteText = note
          ..suggestedParams = sdkParams)
        .build();
    tx.fee = params.minFee;
    final theirs = tx.toBytes();

    expect(ours, equals(theirs));
  });
}
