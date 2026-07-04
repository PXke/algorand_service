import 'dart:typed_data';

import 'package:algorand_auth_core/algorand_auth_core.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('decode golden address', () {
    const golden = '7777777777777777777777777777777777777777777777777774MSJUVU';
    final addr = AlgorandAddress.decode(golden);
    expect(addr.publicKey, equals(Uint8List.fromList(List.filled(32, 0xFF))));
  });

  test('auth payment tx roundtrip fields', () {
    const sender =
        'VKM6KSCTDHEM6KGEAMSYCNEGIPFJMHDSEMIRAQLK76CJDIRMMDHKAIRMFQ';
    final params = SuggestedParams(
      feePerByte: 10,
      genesisId: 'testnet-v1.0',
      genesisHash: null,
      lastRound: 301,
      minFee: 1000,
    );
    final bytes = AuthPaymentTransaction.buildUnsignedBytes(
      senderAddress: sender,
      note: 'login',
      params: params,
      feeMicroAlgos: 0,
    );
    expect(bytes, isNotEmpty);
    expect(bytes.first, greaterThan(0)); // msgpack map header
  });
}
