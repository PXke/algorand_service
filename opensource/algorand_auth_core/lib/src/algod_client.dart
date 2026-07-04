import 'dart:convert';

import 'package:http/http.dart' as http;

import 'suggested_params.dart';

/// Thin algod REST client (http only — no dio).
class AlgodClient {
  AlgodClient({required this.apiUrl, http.Client? client})
      : _client = client ?? http.Client();

  final String apiUrl;
  final http.Client _client;

  Future<SuggestedParams> suggestedParams() async {
    final uri = Uri.parse('${apiUrl.replaceAll(RegExp(r'/+$'), '')}'
        '/v2/transactions/params');
    final response = await _client
        .get(uri)
        .timeout(const Duration(seconds: 15));
    if (response.statusCode != 200) {
      throw AlgodException(
        'algod params failed: HTTP ${response.statusCode}',
      );
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return SuggestedParams.fromJson(body);
  }
}

class AlgodException implements Exception {
  AlgodException(this.message);
  final String message;
  @override
  String toString() => message;
}
