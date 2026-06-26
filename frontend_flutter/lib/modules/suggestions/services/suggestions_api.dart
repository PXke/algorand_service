import '../../../core/api/api_client.dart';

class SuggestionsApi {
  SuggestionsApi(this._client, {Map<String, String> headers = const {}})
      : _headers = headers;

  final ApiClient _client;
  final Map<String, String> _headers;

  Future<Map<String, dynamic>> fetchConfig() async {
    return _client.getJson('/api/v1/suggestions/config');
  }

  Future<List<Map<String, dynamic>>> listOpen() async {
    final body = await _client.getJson('/api/v1/suggestions', headers: _headers);
    final items = body['items'];
    if (items is! List) {
      return const [];
    }
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>> create({
    required String title,
    required String body,
    required String submissionTxid,
  }) async {
    return _client.postJson(
      '/api/v1/suggestions',
      headers: _headers,
      body: {
        'title': title,
        'body': body,
        'submission_txid': submissionTxid,
      },
    );
  }

  Future<Map<String, dynamic>> upvoteMessage(String suggestionId) async {
    return _client.getJson(
      '/api/v1/suggestions/$suggestionId/upvote-message',
      headers: _headers,
    );
  }

  Future<Map<String, dynamic>> upvote({
    required String suggestionId,
    required String signatureB64,
  }) async {
    return _client.postJson(
      '/api/v1/suggestions/$suggestionId/upvote',
      headers: _headers,
      body: {'signature_b64': signatureB64},
    );
  }
}
