import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

/// Client-side x402 "exact" scheme payment flow (AVM). No official Dart/
/// Flutter x402 package exists, so this replicates the wire format verified
/// against the installed Python `x402-avm==2.0.2` package's client scheme
/// (x402/mechanisms/avm/exact/client.py) rather than the (previously
/// unreliable) docs: request the resource, read the 402's PAYMENT-REQUIRED
/// header, sign a payment transaction, retry with PAYMENT-SIGNATURE.
///
/// UNTESTED against a live facilitator (the TestNet round trip this would
/// normally be proven against was explicitly skipped) — treat this as a
/// best-effort implementation of a spec that has already proven unreliable
/// in its own documentation once (see client.py's tag-injection root cause).
class X402PaymentError implements Exception {
  X402PaymentError(this.message);
  final String message;

  @override
  String toString() => 'X402PaymentError: $message';
}

/// Thrown when the server's PaymentRequirements ask for fee abstraction
/// (extra.feePayer set) — that mode needs a 2-txn atomic group with a
/// facilitator-signed fee-payer leg, deliberately not implemented here
/// (real risk of a subtle group-id/encoding bug with no way to verify it
/// against a live facilitator in this pass). Falls back cleanly rather than
/// silently building something wrong.
class X402FeeAbstractionNotSupported implements X402PaymentError {
  @override
  String get message => 'This endpoint requires fee-abstraction payment, which this '
      'app does not yet support.';

  @override
  String toString() => 'X402FeeAbstractionNotSupported: $message';
}

class X402Client {
  X402Client({required this.baseUrl, required this.connector});

  final String baseUrl;
  final WalletConnectAlgorandConnector connector;

  /// GET `path` behind an x402 paywall, paying with `payerAddress` if a 402
  /// is returned. Returns the final decoded JSON body.
  Future<Map<String, dynamic>> getPaid(String path, {required String payerAddress}) async {
    final uri = Uri.parse('$baseUrl$path');
    final first = await http.get(uri);
    if (first.statusCode != 402) {
      return _decodeOrThrow(first);
    }

    final headerValue = first.headers['payment-required'];
    if (headerValue == null) {
      throw X402PaymentError('Server returned 402 without a PAYMENT-REQUIRED header');
    }
    final paymentRequired = jsonDecode(utf8.decode(base64.decode(_pad(headerValue))))
        as Map<String, dynamic>;
    final accepts = paymentRequired['accepts'] as List?;
    if (accepts == null || accepts.isEmpty) {
      throw X402PaymentError('PAYMENT-REQUIRED carried no accepted payment options');
    }
    final requirements = accepts.first as Map<String, dynamic>;

    final extra = (requirements['extra'] as Map?)?.cast<String, dynamic>() ?? const {};
    if (extra['feePayer'] != null) {
      throw X402FeeAbstractionNotSupported();
    }

    final assetId = int.parse(requirements['asset'] as String);
    final amount = int.parse(requirements['amount'] as String);
    final payTo = requirements['payTo'] as String;

    final signedTxnB64 = await connector.signAssetTransferTxn(
      senderAddress: payerAddress,
      receiverAddress: payTo,
      assetId: assetId,
      amount: amount,
      note: 'x402-payment-${DateTime.now().microsecondsSinceEpoch}',
    );

    final payload = {
      'x402Version': paymentRequired['x402Version'] ?? 2,
      'payload': {
        'paymentGroup': [signedTxnB64],
        'paymentIndex': 0,
      },
      'accepted': requirements,
    };
    final signatureHeader = base64.encode(utf8.encode(jsonEncode(payload)));

    final second = await http.get(uri, headers: {'PAYMENT-SIGNATURE': signatureHeader});
    return _decodeOrThrow(second);
  }

  Map<String, dynamic> _decodeOrThrow(http.Response response) {
    final body = response.body.isEmpty ? <String, dynamic>{} : jsonDecode(response.body);
    if (response.statusCode >= 400) {
      String message = response.body;
      if (body is Map) {
        final error = body['error'];
        if (error is Map && error['message'] is String) {
          message = error['message'] as String;
        }
      }
      throw X402PaymentError('$message (HTTP ${response.statusCode})');
    }
    return (body as Map).cast<String, dynamic>();
  }

  /// Base64 headers here are unpadded per the package's safe_base64 helpers
  /// in some cases — pad defensively before decoding.
  String _pad(String value) {
    final remainder = value.length % 4;
    if (remainder == 0) return value;
    return value + ('=' * (4 - remainder));
  }
}
