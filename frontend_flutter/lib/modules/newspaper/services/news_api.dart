import '../../../core/api/api_client.dart';

class NewsApi {
  NewsApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> fetchStats() async {
    return _client.getJson('/api/v1/news/stats');
  }

  Future<List<Map<String, dynamic>>> fetchFeed({int limit = 50, String? lang}) async {
    final page = await fetchFeedPage(limit: limit, lang: lang);
    return page.items;
  }

  Future<FeedPage> fetchFeedPage(
      {int limit = 50, int? cursor, String? serviceId, String? tag, String? lang}) async {
    final params = <String, String>{'limit': '$limit'};
    if (cursor != null) params['cursor'] = '$cursor';
    if (serviceId != null && serviceId.isNotEmpty) params['service_id'] = serviceId;
    if (tag != null && tag.isNotEmpty) params['tag'] = tag;
    if (lang != null && lang.isNotEmpty && lang != 'en') params['lang'] = lang;
    final qs = params.entries
        .map((e) => '${e.key}=${Uri.encodeQueryComponent(e.value)}')
        .join('&');
    final body = await _client.getJson('/api/v1/news/feed?$qs');
    final raw = body['items'];
    final items = raw is List ? raw.whereType<Map<String, dynamic>>().toList() : <Map<String, dynamic>>[];
    final next = body['next_cursor'];
    return FeedPage(items: items, nextCursor: next is int ? next : null);
  }

  /// Reader-engagement ranking; items carry a `views` field the regular feed
  /// omits. [rank] 'hot' = read velocity (views/day since publish),
  /// 'top' = lifetime totals.
  Future<List<Map<String, dynamic>>> fetchHot(
      {int limit = 20, String? lang, String rank = 'hot'}) async {
    final params = <String, String>{'limit': '$limit', 'rank': rank};
    if (lang != null && lang.isNotEmpty && lang != 'en') params['lang'] = lang;
    final qs = params.entries
        .map((e) => '${e.key}=${Uri.encodeQueryComponent(e.value)}')
        .join('&');
    final body = await _client.getJson('/api/v1/news/hot?$qs');
    final raw = body['items'];
    return raw is List
        ? raw.whereType<Map<String, dynamic>>().toList()
        : <Map<String, dynamic>>[];
  }

  /// Per-tag coverage/readership aggregate for the topics cloud:
  /// `{article_count, tags: [{tag, count, views, last_epoch}]}`.
  Future<TagStats> fetchTagStats() async {
    final body = await _client.getJson('/api/v1/news/tags');
    final raw = body['tags'];
    final tags = raw is List
        ? raw.whereType<Map<String, dynamic>>().toList()
        : <Map<String, dynamic>>[];
    final count = body['article_count'];
    return TagStats(articleCount: count is int ? count : 0, tags: tags);
  }

  Future<Map<String, dynamic>> fetchArticle(String articleId, {String? lang}) async {
    String qs = '';
    if (lang != null && lang.isNotEmpty && lang != 'en') qs = '?lang=$lang';
    return _client.getJson('/api/v1/news/articles/$articleId$qs');
  }
}


class FeedPage {
  const FeedPage({required this.items, this.nextCursor});
  final List<Map<String, dynamic>> items;
  final int? nextCursor;
}

class TagStats {
  const TagStats({required this.articleCount, required this.tags});
  final int articleCount;
  final List<Map<String, dynamic>> tags;
}
