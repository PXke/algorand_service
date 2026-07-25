import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config.dart';

class KycApiError implements Exception {
  KycApiError(this.message);
  final String message;

  @override
  String toString() => 'KycApiError: $message';
}

class KycApi {
  KycApi({String? baseUrl}) : baseUrl = baseUrl ?? AppConfig.instance.apiBaseUrl;

  final String baseUrl;

  Future<String> fetchConsentMessage(String walletAddress) async {
    final uri = Uri.parse('$baseUrl/api/v1/kyc/consent-message')
        .replace(queryParameters: {'wallet_address': walletAddress});
    final response = await http.get(uri);
    final body = _decodeOrThrow(response);
    return body['message'] as String;
  }

  Future<Map<String, dynamic>> enroll({
    required String walletAddress,
    required String consentSignatureB64,
  }) async {
    final uri = Uri.parse('$baseUrl/api/v1/kyc/enroll');
    final response = await http.post(
      uri,
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'wallet_address': walletAddress,
        'consent_signature_b64': consentSignatureB64,
      }),
    );
    return _decodeOrThrow(response);
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
      throw KycApiError('$message (HTTP ${response.statusCode})');
    }
    return (body as Map).cast<String, dynamic>();
  }
}
