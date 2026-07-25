import '../../../core/api/api_client.dart';

class AdminApi {
  AdminApi(this._client, {this.sessionToken});

  final ApiClient _client;

  /// Verified session token from sign-in. Admin authorization is enforced on
  /// the server by resolving this token to the signed-in wallet; the wallet
  /// address header is sent only for display/attribution, never trusted.
  final String? sessionToken;

  Map<String, String> _adminHeaders(String walletAddress) => {
        'X-Admin-Wallet': walletAddress,
        if (sessionToken != null && sessionToken!.isNotEmpty)
          'x-session-token': sessionToken!,
      };

  Future<Map<String, dynamic>> patchArticle(
    String articleId, {
    required String walletAddress,
    String? title,
    String? summary,
    String? body,
  }) async {
    final payload = <String, dynamic>{};
    if (title != null) payload['title'] = title;
    if (summary != null) payload['summary'] = summary;
    if (body != null) payload['body'] = body;
    return _client.patchJson(
      '/api/v1/admin/articles/$articleId',
      body: payload,
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> deleteArticle({
    required String walletAddress,
    required String articleId,
    bool blockSource = false,
  }) async {
    final path = blockSource
        ? '/api/v1/admin/articles/$articleId?block_source=true'
        : '/api/v1/admin/articles/$articleId';
    return _client.deleteJson(
      path,
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<List<Map<String, dynamic>>> listBriefs({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/briefs',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<List<Map<String, dynamic>>> listPublishQueue({
    required String walletAddress,
    int limit = 200,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/publish-queue?limit=$limit',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>> publishQueueBreakdown({
    required String walletAddress,
    required String queueId,
  }) async {
    return _client.getJson(
      '/api/v1/admin/publish-queue/$queueId/breakdown',
      headers: _adminHeaders(walletAddress),
    );
  }

  /// Approved articles waiting in pending_feed_queue for paced release
  /// (capped by PENDING_FEED_MAX_DEPTH) — distinct from listPublishQueue,
  /// which shows in-flight composing work.
  Future<List<Map<String, dynamic>>> listPendingFeedBacklog({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/pending-feed-backlog',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<List<Map<String, dynamic>>> listClassifierReviews({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/classifier-reviews',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>> submitClassifierFeedback({
    required String walletAddress,
    required String url,
    required String textSample,
    required String category,
    required String predictedCategory,
    required String quality,
    required bool predictedPublish,
    required bool approved,
    List<String> categories = const [],
    bool sourceRelevant = true,
    bool trainingOnly = false,
    Map<String, double> correctedScores = const {},
    bool anchor = false,
    bool factualityFail = false,
    bool toneFail = false,
    List<String> errorTypes = const [],
    String? reviewId,
    String? articleId,
  }) async {
    final body = <String, dynamic>{
      'url': url,
      'text_sample': textSample,
      'category': category,
      'categories': categories,
      'predicted_category': predictedCategory,
      'quality': quality,
      'source_relevant': sourceRelevant,
      'predicted_publish': predictedPublish,
      'approved': approved,
      'training_only': trainingOnly,
    };
    if (correctedScores.isNotEmpty) {
      body['corrected_scores'] = correctedScores;
    }
    if (anchor) {
      body['anchor'] = true;
      body['factuality_fail'] = factualityFail;
      body['tone_fail'] = toneFail;
      body['error_types'] = errorTypes;
    }
    if (reviewId != null && reviewId.isNotEmpty) {
      body['review_id'] = reviewId;
    }
    if (articleId != null && articleId.isNotEmpty) {
      body['article_id'] = articleId;
    }
    return _client.postJson(
      '/api/v1/admin/classifier-feedback',
      body: body,
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> getTrainingStats({
    required String walletAddress,
  }) async {
    return _client.getJson(
      '/api/v1/admin/training-stats',
      headers: _adminHeaders(walletAddress),
    );
  }

  /// First-party traffic analytics (pageviews split human/bot, top pages and
  /// referrers) over the last [days] days.
  Future<Map<String, dynamic>> fetchAnalytics({
    required String walletAddress,
    int days = 14,
  }) async {
    return _client.getJson(
      '/api/v1/admin/analytics?days=$days',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> triggerRetrain({
    required String walletAddress,
  }) async {
    return _client.postJson(
      '/api/v1/admin/retrain',
      body: const {},
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> upsertSource({
    required String walletAddress,
    required String serviceId,
    required String displayName,
    required String scrapeUrl,
    String matchKind = 'domain',
    String matchValue = '',
    bool enabled = true,
  }) async {
    return _client.postJson(
      '/api/v1/admin/sources',
      body: {
        'service_id': serviceId,
        'display_name': displayName,
        'scrape_url': scrapeUrl,
        'match_kind': matchKind,
        'match_value': matchValue,
        'enabled': enabled,
      },
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> deleteSource({
    required String walletAddress,
    required String serviceId,
  }) async {
    return _client.deleteJson(
      '/api/v1/admin/sources/$serviceId',
      headers: _adminHeaders(walletAddress),
    );
  }

  /// Fold [sourceServiceIds] into [targetServiceId]: their sources move to the
  /// target, their domains re-point, and the emptied services are disabled.
  Future<Map<String, dynamic>> mergeServices({
    required String walletAddress,
    required String targetServiceId,
    required List<String> sourceServiceIds,
  }) async {
    return _client.postJson(
      '/api/v1/admin/sources/merge',
      body: {
        'target_service_id': targetServiceId,
        'source_service_ids': sourceServiceIds,
      },
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<List<Map<String, dynamic>>> listScrapers({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/scrapers',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>> runScraper({
    required String walletAddress,
    required String action,
  }) async {
    return _client.postJson(
      '/api/v1/admin/scrapers/run',
      body: {'action': action},
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<List<Map<String, dynamic>>> celeryWorkers({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/celery',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['workers'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<Map<String, dynamic>> resetPipeline({
    required String walletAddress,
  }) async {
    return _client.postJson(
      '/api/v1/admin/articles/reset',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<List<Map<String, dynamic>>> investigationFindings({
    required String walletAddress,
    required String url,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/investigations?url=${Uri.encodeQueryComponent(url)}',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<List<Map<String, dynamic>>> listToolSuggestions({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/tool-suggestions',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  Future<List<Map<String, dynamic>>> listComposeFeedback({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/compose-feedback',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  /// Summary only (status/timing) — no messages/final_output. Cheap enough to
  /// poll; fetch a transcript on demand via [getComposeSessionDetail].
  Future<List<Map<String, dynamic>>> listComposeSessions({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/compose-sessions',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }

  /// Full transcript (messages + final_output) for one session. `createdAt`
  /// must be the exact ISO string from the session's list entry (it's part of
  /// the Cassandra row key alongside sessionId).
  Future<Map<String, dynamic>> getComposeSessionDetail({
    required String walletAddress,
    required String sessionId,
    required String createdAt,
  }) async {
    final query = Uri(queryParameters: {'created_at': createdAt}).query;
    return _client.getJson(
      '/api/v1/admin/compose-sessions/$sessionId?$query',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> composeNextReview({
    required String walletAddress,
  }) async {
    return _client.postJson(
      '/api/v1/admin/classifier-reviews/compose-next',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> recomposeReview({
    required String walletAddress,
    required String reviewId,
  }) async {
    return _client.postJson(
      '/api/v1/admin/classifier-reviews/recompose',
      body: {'review_id': reviewId},
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> backfillArticleTranslations({
    required String walletAddress,
    int limit = 500,
  }) async {
    return _client.postJson(
      '/api/v1/admin/translations/backfill',
      body: {'limit': limit},
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> clearClassifierReviews({
    required String walletAddress,
  }) async {
    return _client.postJson(
      '/api/v1/admin/classifier-reviews/clear',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<({List<Map<String, dynamic>> items, int autoApprovedToday, int total})> listDomains({
    required String walletAddress,
    String? status,
    int page = 0,
    int pageSize = 25,
  }) async {
    final params = <String>[
      if (status != null && status.isNotEmpty && status != 'all') 'status=$status',
      'page=$page',
      'page_size=$pageSize',
    ];
    final body = await _client.getJson(
      '/api/v1/admin/domains?${params.join('&')}',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    final list = items is List ? items.whereType<Map<String, dynamic>>().toList() : <Map<String, dynamic>>[];
    final auto = body['auto_approved_today'];
    final total = body['total'];
    return (
      items: list,
      autoApprovedToday: auto is int ? auto : 0,
      total: total is int ? total : list.length,
    );
  }

  Future<Map<String, dynamic>> setDomainRelevant({
    required String walletAddress,
    required String domain,
    required bool isRelevant,
    // False approves the domain for one-time frontier crawling only — no
    // permanent monitored source gets created, so it won't be repeatedly
    // re-scraped going forward. Defaults true to match prior behavior.
    bool asSeed = true,
    // True: this one page is a citation, not "watch this whole domain" —
    // fetches only this URL, never follows its links, and is excluded from
    // every future domain-wide sweep. Implies asSeed=false server-side.
    bool singlePageOnly = false,
  }) async {
    return _client.postJson(
      '/api/v1/admin/domains/set',
      body: {
        'domain': domain,
        'is_relevant': isRelevant,
        'as_seed': asSeed,
        'single_page_only': singlePageOnly,
      },
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> clearDomains({
    required String walletAddress,
  }) async {
    return _client.postJson(
      '/api/v1/admin/domains/clear',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> createBrief({
    required String walletAddress,
    required String title,
    required String bodyMarkdown,
    String keywords = '',
    String status = 'active',
    int refreshEveryDays = 0,
  }) async {
    return _client.postJson(
      '/api/v1/admin/briefs',
      body: {
        'title': title,
        'body_markdown': bodyMarkdown,
        'keywords': keywords,
        'status': status,
        'refresh_every_days': refreshEveryDays,
      },
      headers: _adminHeaders(walletAddress),
    );
  }

  /// Triggers the writer now for this brief — a fresh assignment if it has no
  /// article yet, or an in-place refresh of its existing article otherwise
  /// (decided server-side).
  Future<Map<String, dynamic>> assignBriefNow({
    required String walletAddress,
    required String briefId,
  }) async {
    return _client.postJson(
      '/api/v1/admin/briefs/$briefId/assign-now',
      headers: _adminHeaders(walletAddress),
    );
  }

  // --- Gatekeeper validation anchors -------------------------------------
  Future<Map<String, dynamic>> listGatekeeperAnchors({
    required String walletAddress,
  }) async {
    return _client.getJson(
      '/api/v1/admin/gatekeeper/anchors',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> addGatekeeperAnchor({
    required String walletAddress,
    required String articleId,
    bool factualityFail = false,
    bool toneFail = false,
    List<String> errorTypes = const [],
  }) async {
    return _client.postJson(
      '/api/v1/admin/gatekeeper/anchor',
      body: {
        'article_id': articleId,
        'factuality_fail': factualityFail,
        'tone_fail': toneFail,
        'error_types': errorTypes,
      },
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> runGatekeeperValidation({
    required String walletAddress,
  }) async {
    return _client.postJson(
      '/api/v1/admin/gatekeeper/validate',
      body: const {},
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<Map<String, dynamic>> getGatekeeperValidationReport({
    required String walletAddress,
  }) async {
    return _client.getJson(
      '/api/v1/admin/gatekeeper/validation-report',
      headers: _adminHeaders(walletAddress),
    );
  }

  Future<List<Map<String, dynamic>>> listContactMessages({
    required String walletAddress,
  }) async {
    final body = await _client.getJson(
      '/api/v1/admin/contact-messages',
      headers: _adminHeaders(walletAddress),
    );
    final items = body['items'];
    if (items is! List) return const [];
    return items.whereType<Map<String, dynamic>>().toList();
  }
}
