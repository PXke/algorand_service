import '../../../core/api/api_client.dart';

class RegistryApi {
  RegistryApi(this._client);

  final ApiClient _client;

  Future<List<Map<String, dynamic>>> fetchServices({bool seedsOnly = true}) async {
    final path = seedsOnly
        ? '/api/v1/registry/services?seeds_only=1'
        : '/api/v1/registry/services';
    final body = await _client.getJson(path);
    final items = body['items'];
    if (items is! List) {
      return const [];
    }
    return items.whereType<Map<String, dynamic>>().toList();
  }
}
