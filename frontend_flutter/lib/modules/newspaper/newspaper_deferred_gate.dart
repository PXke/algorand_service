import 'package:flutter/material.dart';

import 'newspaper_entry.dart' deferred as newspaper;

/// Memoized load for the newspaper deferred chunk. Callers must go through
/// [loadDeferredWithRetry] / [serializeDeferredLoad] — do not nest serialize here
/// or the global queue deadlocks.
Future<void>? _library;

Future<void> loadNewspaperModule() => _library ??= newspaper.loadLibrary();

Widget buildNewsPage() => newspaper.NewsPage();

Widget buildArticleDetailPage({required String articleId}) =>
    newspaper.ArticleDetailPage(articleId: articleId);

Widget buildHotPage() => newspaper.HotPage();

Widget buildTopicsPage() => newspaper.TopicsPage();

Widget buildTopicPage({required String tag}) => newspaper.TopicPage(tag: tag);
