import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/app_config.dart';

class ApiClient {
  ApiClient({http.Client? client, AppConfig? config})
      : _client = client ?? http.Client(),
        _config = config ?? AppConfig.instance;

  final http.Client _client;
  final AppConfig _config;

  Uri _uri(String path) => Uri.parse('${_config.apiBaseUrl}$path');

  Future<Map<String, dynamic>> postJson(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? headers,
  }) async {
    try {
      final response = await _client.post(
        _uri(path),
        headers: {
          'Content-Type': 'application/json',
          ...?headers,
        },
        body: jsonEncode(body ?? {}),
      );
      return _decode(response);
    } catch (e) {
      throw _wrapNetworkError(e);
    }
  }

  Future<Map<String, dynamic>> patchJson(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? headers,
  }) async {
    try {
      final response = await _client.patch(
        _uri(path),
        headers: {
          'Content-Type': 'application/json',
          ...?headers,
        },
        body: jsonEncode(body ?? {}),
      );
      return _decode(response);
    } catch (e) {
      throw _wrapNetworkError(e);
    }
  }

  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, String>? headers,
  }) async {
    try {
      final response = await _client.get(
        _uri(path),
        headers: headers,
      );
      return _decode(response);
    } catch (e) {
      throw _wrapNetworkError(e);
    }
  }

  Future<Map<String, dynamic>> deleteJson(
    String path, {
    Map<String, String>? headers,
  }) async {
    try {
      final response = await _client.delete(
        _uri(path),
        headers: headers,
      );
      return _decode(response);
    } catch (e) {
      throw _wrapNetworkError(e);
    }
  }

  ApiException _wrapNetworkError(Object error) {
    if (error is ApiException) {
      return error;
    }
    final base = _config.apiBaseUrl;
    return ApiException(
      0,
      'network_error',
      'Cannot reach the API at $base. Start the backend (e.g. make docker-app) or check API_BASE_URL.',
    );
  }

  Map<String, dynamic> _decode(http.Response response) {
    Map<String, dynamic> decoded;
    try {
      final raw = jsonDecode(response.body);
      if (raw is! Map<String, dynamic>) {
        throw ApiException(
          response.statusCode,
          'invalid_response',
          'Server returned an unexpected response format',
        );
      }
      decoded = raw;
    } on FormatException {
      throw ApiException(
        response.statusCode,
        'invalid_response',
        response.statusCode >= 400
            ? 'Server error (${response.statusCode})'
            : 'Server returned invalid JSON',
      );
    }

    if (response.statusCode >= 400) {
      throw ApiException.fromBody(response.statusCode, decoded);
    }
    return decoded;
  }
}

class ApiException implements Exception {
  ApiException(this.statusCode, this.code, this.message);

  factory ApiException.fromBody(int statusCode, Map<String, dynamic> body) {
    final err = body['error'];
    if (err is Map<String, dynamic>) {
      return ApiException(
        statusCode,
        err['code']?.toString() ?? 'unknown_error',
        err['message']?.toString() ?? body['detail']?.toString() ?? 'Request failed',
      );
    }
    return ApiException(
      statusCode,
      err?.toString() ?? 'unknown_error',
      body['detail']?.toString() ?? body['message']?.toString() ?? 'Request failed',
    );
  }

  final int statusCode;
  final String code;
  final String message;

  String get userMessage => message.isNotEmpty ? message : code;

  @override
  String toString() => 'ApiException($statusCode, $code): $message';
}
