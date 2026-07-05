import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/wallet_auth_config.dart';
import '../models/auth_models.dart';
import 'auth_api.dart';

class AuthApiException implements Exception {
  AuthApiException(this.statusCode, this.message);
  final int statusCode;
  final String message;

  @override
  String toString() => 'AuthApiException($statusCode): $message';
}

/// Default HTTP implementation matching the platform Robyn auth routes.
class HttpAuthApi implements AuthApi {
  HttpAuthApi({
    required WalletAuthConfig config,
    http.Client? client,
  })  : _config = config,
        _client = client ?? http.Client();

  final WalletAuthConfig _config;
  final http.Client _client;

  Uri _uri(String path) => Uri.parse('${_config.apiBaseUrl}$path');

  @override
  Future<AuthNonce> requestNonce(String walletAddress) async {
    final json = await _post('/api/v1/auth/nonce', body: {'wallet_address': walletAddress});
    return AuthNonce.fromJson(json);
  }

  @override
  Future<AuthSession> verifyLogin({
    required String walletAddress,
    required String nonce,
    required WalletAuthProof proof,
  }) async {
    final body = <String, dynamic>{
      'wallet_address': walletAddress,
      'nonce': nonce,
      'proof_method': proof.method.apiValue,
    };

    switch (proof.method) {
      case AuthProofMethod.arc0060:
        body['arc0060'] = proof.arc0060!.toVerifyJson();
      case AuthProofMethod.arc0025Txn:
        body['signed_txn_b64'] = proof.signedTxnBase64;
      case AuthProofMethod.legacyMessage:
        body['signature_b64'] = proof.signedTxnBase64;
      case AuthProofMethod.signedBytes:
        body['signature_b64'] = proof.signatureBase64;
    }

    final json = await _post('/api/v1/auth/verify-wallet-signature', body: body);
    return AuthSession.fromJson(json);
  }

  @override
  Future<AuthSession> verifyWithSignedTransaction({
    required String walletAddress,
    required String nonce,
    required String signedTxnBase64,
  }) {
    return verifyLogin(
      walletAddress: walletAddress,
      nonce: nonce,
      proof: WalletAuthProof.arc0025Txn(signedTxnBase64),
    );
  }

  @override
  Future<AuthSession> verifyWithMessageSignature({
    required String walletAddress,
    required String nonce,
    required String signatureBase64,
  }) async {
    final json = await _post(
      '/api/v1/auth/verify-wallet-signature',
      body: {
        'wallet_address': walletAddress,
        'nonce': nonce,
        'proof_method': AuthProofMethod.legacyMessage.apiValue,
        'signature_b64': signatureBase64,
      },
    );
    return AuthSession.fromJson(json);
  }

  @override
  Future<Map<String, dynamic>> getSession(String sessionToken) async {
    return _get('/api/v1/auth/session', headers: {'x-session-token': sessionToken});
  }

  @override
  Future<void> logout(String sessionToken) async {
    await _post('/api/v1/auth/logout', headers: {'x-session-token': sessionToken});
  }

  Future<Map<String, dynamic>> _post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? headers,
  }) async {
    final response = await _client.post(
      _uri(path),
      headers: {'Content-Type': 'application/json', ...?headers},
      body: jsonEncode(body ?? {}),
    );
    return _decode(response);
  }

  Future<Map<String, dynamic>> _get(String path, {Map<String, String>? headers}) async {
    final response = await _client.get(_uri(path), headers: headers);
    return _decode(response);
  }

  Map<String, dynamic> _decode(http.Response response) {
    final decoded = jsonDecode(response.body);
    if (decoded is! Map<String, dynamic>) {
      throw AuthApiException(response.statusCode, 'Expected JSON object');
    }
    if (response.statusCode >= 400) {
      throw AuthApiException(
        response.statusCode,
        decoded['error']?.toString() ?? response.body,
      );
    }
    return decoded;
  }
}
