import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/highlight_text.dart';
import '../../../core/ui/hover_card.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/ui/page_header.dart';
import '../services/search_api.dart';

class SearchPage extends ConsumerStatefulWidget {
  const SearchPage({super.key});

  @override
  ConsumerState<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends ConsumerState<SearchPage> {
  static const _debounce = Duration(milliseconds: 450);

  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  Timer? _debounceTimer;
  List<Map<String, dynamic>> _items = const [];
  String? _engine;
  String? _error;
  bool _loading = false;
  bool _searched = false;

  @override
  void initState() {
    super.initState();
    // Honour a ?q= deep link (e.g. the Google sitelinks search box, whose
    // SearchAction target points here) — prefill and run the search on load.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final q = GoRouterState.of(context).uri.queryParameters['q']?.trim() ?? '';
      if (q.isNotEmpty) {
        _controller.text = q;
        _search();
      }
      _focusNode.requestFocus();
    });
  }

  @override
  void dispose() {
    _debounceTimer?.cancel();
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onQueryChanged(String value) {
    _debounceTimer?.cancel();
    if (value.trim().isEmpty) {
      setState(() {});
      return;
    }
    setState(() {});
    _debounceTimer = Timer(_debounce, _search);
  }

  void _clearQuery() {
    _debounceTimer?.cancel();
    _controller.clear();
    setState(() {
      _items = const [];
      _engine = null;
      _error = null;
      _searched = false;
    });
    _focusNode.requestFocus();
  }

  Future<void> _search() async {
    final q = _controller.text.trim();
    if (q.isEmpty) return;
    setState(() {
      _loading = true;
      _error = null;
      _searched = true;
    });
    try {
      final body = await SearchApi(ref.read(apiClientProvider)).search(query: q);
      if (!mounted) return;
      setState(() {
        _engine = body['engine']?.toString();
        _items = (body['items'] as List<dynamic>? ?? const [])
            .whereType<Map<String, dynamic>>()
            .toList();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = apiErrorMessage(e);
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;

    return PageScroll(
      children: [
        PageHeader(
          title: l10n.searchTitle,
          subtitle: l10n.searchSubtitle,
        ),
        DecoratedBox(
          decoration: BoxDecoration(
            color: colors.panelBackground,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: colors.border),
            boxShadow: [
              BoxShadow(
                color: colors.cardShadow,
                blurRadius: 16,
                offset: const Offset(0, 6),
              ),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: TextField(
                    controller: _controller,
                    focusNode: _focusNode,
                    style: theme.textTheme.bodyLarge?.copyWith(fontSize: 17),
                    decoration: InputDecoration(
                      labelText: l10n.searchQueryLabel,
                      hintText: l10n.searchQueryHint,
                      prefixIcon: Icon(Icons.search_rounded, color: theme.colorScheme.primary),
                      suffixIcon: _controller.text.isEmpty
                          ? null
                          : IconButton(
                              icon: const Icon(Icons.clear_rounded, size: 20),
                              tooltip: MaterialLocalizations.of(context).deleteButtonTooltip,
                              onPressed: _clearQuery,
                            ),
                      filled: true,
                      fillColor: theme.scaffoldBackgroundColor,
                    ),
                    onChanged: _onQueryChanged,
                    onSubmitted: (_) {
                      _debounceTimer?.cancel();
                      _search();
                    },
                  ),
                ),
                const SizedBox(width: 14),
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: FilledButton.icon(
                    onPressed: _loading ? null : _search,
                    icon: const Icon(Icons.arrow_forward_rounded, size: 18),
                    label: Text(l10n.searchAction),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: AppLayout.sectionGap),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        // The backing engine (typesense / feed_scan) is an implementation
        // detail — readers never see it; 'error' still surfaces a message below.
        if (_engine == 'error' && _error == null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(l10n.searchErrorBackend, style: theme.textTheme.bodySmall),
          ),
        if (_searched && !_loading && _items.isEmpty && _error == null)
          EmptyState(
            title: l10n.searchEmptyTitle,
            message: l10n.searchEmptyMessage,
            icon: Icons.search_off_outlined,
          ),
        ..._items.map((item) {
          final id = item['article_id']?.toString() ?? '';
          final title = item['title_highlight']?.toString().trim().isNotEmpty == true
              ? item['title_highlight']!.toString()
              : item['title']?.toString() ?? '';
          final excerpt = item['snippet']?.toString().trim().isNotEmpty == true
              ? item['snippet']!.toString()
              : item['summary']?.toString() ?? '';
          return Padding(
            padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
            child: HoverCard(
              onTap: id.isEmpty ? null : () => context.go('/news/articles/$id'),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(22, 18, 18, 18),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 40,
                      height: 40,
                      decoration: BoxDecoration(
                        color: colors.accentSoft,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Icon(Icons.article_outlined, size: 20, color: colors.accent),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          HighlightText(
                            title,
                            style: theme.textTheme.titleLarge?.copyWith(
                              fontSize: 19,
                              height: 1.3,
                            ),
                          ),
                          const SizedBox(height: 8),
                          HighlightText(
                            excerpt,
                            maxLines: 4,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.bodyMedium?.copyWith(
                              height: 1.5,
                              color: colors.muted,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    Icon(Icons.chevron_right_rounded, color: colors.subtle),
                  ],
                ),
              ),
            ),
          );
        }),
      ],
    );
  }
}
