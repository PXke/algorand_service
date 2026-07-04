import 'dart:convert';
import 'dart:typed_data';

import 'address.dart';
import 'msgpack.dart';
import 'suggested_params.dart';

/// Builds the unsigned 0-ALGO self-payment ARC-0025 auth transaction bytes.
class AuthPaymentTransaction {
  /// Unsigned transaction msgpack bytes (what wallets sign via `algo_signTxn`).
  static Uint8List buildUnsignedBytes({
    required String senderAddress,
    required String note,
    required SuggestedParams params,
    int feeMicroAlgos = 0,
  }) {
    final sender = AlgorandAddress.decode(senderAddress);
    final noteBytes = Uint8List.fromList(utf8.encode(note));

    final fields = <String, dynamic>{
      'type': 'pay',
      'snd': sender.publicKey,
      'rcv': sender.publicKey,
      'fv': params.lastRound,
      'lv': params.lastRound + 1000,
      'gen': params.genesisId,
      'gh': params.genesisHash,
      'note': noteBytes,
      if (feeMicroAlgos != 0) 'fee': feeMicroAlgos,
    };

    return AlgorandMsgpack.encode(fields);
  }
}
