import 'dart:convert';
import 'dart:typed_data';

import 'address.dart';
import 'msgpack.dart';
import 'suggested_params.dart';

/// Builds unsigned ASA-transfer transaction bytes (what wallets sign via
/// `algo_signTxn`) — e.g. for an x402 "exact" scheme payment in USDC.
/// Unlike [AuthPaymentTransaction] (a login proof, never broadcast, so its
/// fee is deliberately zeroed), this transaction is meant to actually be
/// submitted, so it carries a real fee.
class AssetTransferTransaction {
  static Uint8List buildUnsignedBytes({
    required String senderAddress,
    required String receiverAddress,
    required int assetId,
    required int amount,
    required SuggestedParams params,
    String? note,
    int? feeMicroAlgos,
  }) {
    final sender = AlgorandAddress.decode(senderAddress);
    final receiver = AlgorandAddress.decode(receiverAddress);

    final fields = <String, dynamic>{
      'type': 'axfer',
      'snd': sender.publicKey,
      'arcv': receiver.publicKey,
      'aamt': amount,
      'xaid': assetId,
      'fee': feeMicroAlgos ?? params.minFee,
      'fv': params.lastRound,
      'lv': params.lastRound + 1000,
      'gen': params.genesisId,
      'gh': params.genesisHash,
      if (note != null) 'note': Uint8List.fromList(utf8.encode(note)),
    };

    return AlgorandMsgpack.encode(fields);
  }
}
