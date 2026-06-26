import '../../../core/api/api_client.dart';

class NewsApi {
  NewsApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> fetchStats() async {
    return _client.getJson('/api/v1/news/stats');
  }

  Future<List<Map<String, dynamic>>> fetchFeed({int limit = 50}) async {
    final page = await fetchFeedPage(limit: limit);
    return page.items;
  }

  Future<FeedPage> fetchFeedPage({int limit = 50, int? cursor, String? serviceId}) async {
    final params = <String, String>{'limit': '$limit'};
    if (cursor != null) params['cursor'] = '$cursor';
    if (serviceId != null && serviceId.isNotEmpty) params['service_id'] = serviceId;
    final qs = params.entries
        .map((e) => '${e.key}=${Uri.encodeQueryComponent(e.value)}')
        .join('&');
    final body = await _client.getJson('/api/v1/news/feed?$qs');
    final raw = body['items'];
    final items = raw is List ? raw.whereType<Map<String, dynamic>>().toList() : <Map<String, dynamic>>[];
    final next = body['next_cursor'];
    return FeedPage(items: items, nextCursor: next is int ? next : null);
  }

  Future<Map<String, dynamic>> fetchArticle(String articleId) async {
    return _client.getJson('/api/v1/news/articles/$articleId');
  }
}


class FeedPage {
  const FeedPage({required this.items, this.nextCursor});
  final List<Map<String, dynamic>> items;
  final int? nextCursor;
}
