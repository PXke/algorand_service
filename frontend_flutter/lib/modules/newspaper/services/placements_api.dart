import '../../../core/api/api_client.dart';

class PlacementsApi {
  PlacementsApi(this._client);

  final ApiClient _client;

  Future<List<Map<String, dynamic>>> fetchPlacements({
    String slot = 'news_feed_inline',
    int limit = 5,
  }) async {
    final body = await _client.getJson(
      '/api/v1/news/placements?slot=$slot&limit=$limit',
    );
    final items = body['items'];
    if (items is! List) {
      return const [];
    }
    return items.whereType<Map<String, dynamic>>().toList();
  }
}
