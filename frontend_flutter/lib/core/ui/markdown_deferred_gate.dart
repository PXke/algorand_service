import 'package:flutter/material.dart';

import 'markdown_entry.dart' deferred as md;

/// Single deferred-import site for markdown. Admin and article detail both
/// route through here so dart2js does not emit duplicate markdown `.part.js`
/// payloads.
Future<void>? _library;

Future<void> loadMarkdownModule() => _library ??= md.loadLibrary();

Widget buildArticleMarkdown({
  required String data,
  bool selectable = true,
}) =>
    md.ArticleMarkdown(data: data, selectable: selectable);
