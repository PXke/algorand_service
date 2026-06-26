import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:algorand_dart/algorand_dart.dart';
import 'package:crypto/crypto.dart';

import '../caip122/caip122_message.dart';

/// ARC-0060 AUTH scope metadata.
const int arc0060ScopeAuth = 1;
const String arc0060EncodingBase64 = 'base64';

/// Signed ARC-0060 AUTH proof for backend verification.
class Arc0060Proof {
  const Arc0060Proof({
    required this.dataB64,
    required this.signatureB64,
    required this.authenticatorDataB64,
    required this.domain,
    this.requestId,
  });

  final String dataB64;
  final String signatureB64;
  final String authenticatorDataB64;
  final String domain;
  final String? requestId;

  Map<String, dynamic> toVerifyJson() => {
        'data_b64': dataB64,
        'signature_b64': signatureB64,
        'authenticator_data_b64': authenticatorDataB64,
        'domain': domain,
        if (requestId != null) 'request_id': requestId,
      };
}

/// Build minimal WebAuthn-style authenticatorData (rpIdHash only).
Uint8List buildAuthenticatorData(String domain) {
  final rpIdHash = sha256.convert(utf8.encode(domain)).bytes;
  return Uint8List.fromList(rpIdHash);
}

/// Build [StdSigData] request map for WalletConnect `algo_signData` / `signData`.
Map<String, dynamic> buildArc0060SignRequest({
  required Caip122Message caip122,
  required String walletAddress,
  String? requestId,
}) {
  final address = Address.fromAlgorandAddress(address: walletAddress);
  final id = requestId ?? _randomBase64(32);
  final authData = buildAuthenticatorData(caip122.domain);

  return {
    'data': caip122.toDataBase64(),
    'signer': base64Encode(address.publicKey),
    'domain': caip122.domain,
    'requestId': id,
    'authenticatorData': base64Encode(authData),
  };
}

Arc0060Proof? parseArc0060SignResponse(
  dynamic result, {
  required String domain,
}) {
  if (result is! Map) return null;
  final signature = result['signature'];
  final data = result['data'];
  final auth = result['authenticatorData'] ?? result['authenticationData'];
  if (signature == null || data == null || auth == null) return null;

  return Arc0060Proof(
    dataB64: _toBase64(data),
    signatureB64: _toBase64(signature),
    authenticatorDataB64: _toBase64(auth),
    domain: (result['domain'] as String?) ?? domain,
    requestId: result['requestId'] as String?,
  );
}

String _toBase64(dynamic value) {
  if (value is String) return value;
  if (value is List<int>) return base64Encode(value);
  if (value is Uint8List) return base64Encode(value);
  throw FormatException('Expected base64 string or bytes, got ${value.runtimeType}');
}

String _randomBase64(int numBytes) {
  final random = Random.secure();
  final bytes = List<int>.generate(numBytes, (_) => random.nextInt(256));
  return base64Encode(bytes);
}
