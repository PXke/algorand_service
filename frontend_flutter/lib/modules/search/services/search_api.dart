import '../../../core/api/api_client.dart';

class SearchApi {
  SearchApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> search({
    required String query,
    int limit = 20,
    String? serviceId,
  }) async {
    final params = <String, String>{
      'q': query,
      'limit': '$limit',
      if (serviceId != null && serviceId.isNotEmpty) 'service_id': serviceId,
    };
    final queryString = params.entries.map((e) => '${e.key}=${Uri.encodeComponent(e.value)}').join('&');
    return _client.getJson('/api/v1/search?$queryString');
  }
}
