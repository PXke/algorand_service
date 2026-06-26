import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../auth/providers/auth_providers.dart';
import '../models/classifier_labels.dart';
import '../../../core/ui/article_markdown.dart';
import '../../newspaper/services/news_api.dart';

/// Admin tab: approve/reject classifier review queue items with category + quality.
class ClassifierFeedbackTab extends ConsumerStatefulWidget {
  const ClassifierFeedbackTab({super.key});

  @override
  ConsumerState<ClassifierFeedbackTab> createState() => _ClassifierFeedbackTabState();
}

class _ClassifierFeedbackTabState extends ConsumerState<ClassifierFeedbackTab> {
  List<Map<String, dynamic>> _items = const [];
  bool _loading = true;
  // Training mode: accept/reject still records the label (both models learn) but
  // an accepted article is NOT published — for the bootstrap labelling sprint.
  bool _trainingMode = false;
  String? _error;
  final Set<String> _pending = {};
  final Map<String, Set<String>> _selectedCategories = {};
  final Map<String, String> _selectedQuality = {};
  final Map<String, bool> _sourceRelevant = {};
  // Per-dimension score overrides (0-10) for the current review item — only the
  // ones the reviewer dragged; reset when the item changes.
  final Map<String, double> _editedScores = {};
  // Gatekeeper validation-anchor tagging (one-time, per item).
  final Map<String, bool> _anchor = {};
  final Map<String, bool> _anchorFactFail = {};
  final Map<String, bool> _anchorToneFail = {};
  final Map<String, Set<String>> _anchorTypes = {};
  String? _topBody;
  List<String> _topTags = const [];
  String? _topBodyForId;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  String _itemKey(Map<String, dynamic> item) =>
      item['review_id']?.toString() ?? item['url']?.toString() ?? '';

  Set<String> _categoriesFor(Map<String, dynamic> item) {
    final key = _itemKey(item);
    final predicted = (item['category'] as String? ?? 'generic').toLowerCase();
    return _selectedCategories.putIfAbsent(
      key,
      () => {classifierCategories.contains(predicted) ? predicted : 'generic'},
    );
  }

  bool _sourceRelevantFor(Map<String, dynamic> item) =>
      _sourceRelevant.putIfAbsent(_itemKey(item), () => true);

  String _qualityFor(Map<String, dynamic> item) {
    final key = _itemKey(item);
    return _selectedQuality.putIfAbsent(key, () => 'medium');
  }

  Future<void> _load() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) {
      setState(() {
        _loading = false;
        _error = 'Wallet not connected';
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = ref.read(adminApiProvider);
      final items = await api.listClassifierReviews(walletAddress: wallet);
      items.sort((a, b) => ((b['storage_score'] as num?) ?? 0)
          .compareTo((a['storage_score'] as num?) ?? 0));
      if (!mounted) return;
      setState(() {
        _items = items;
        _editedScores.clear();
        _loading = false;
      });
      await _loadTopBody();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  bool _composing = false;

  Future<void> _composeNext() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null) return;
    setState(() => _composing = true);
    try {
      await ref.read(adminApiProvider).composeNextReview(walletAddress: wallet);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Pulling the top topic — it will appear shortly')),
      );
      // Give the worker a few seconds to compose, then reload.
      await Future.delayed(const Duration(seconds: 6));
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e')));
    } finally {
      if (mounted) setState(() => _composing = false);
    }
  }

  String? _recomposingId;

  Future<void> _recompose(Map<String, dynamic> item) async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null) return;
    final reviewId = item['review_id']?.toString() ?? '';
    if (reviewId.isEmpty) return;
    setState(() => _recomposingId = reviewId);
    try {
      await ref
          .read(adminApiProvider)
          .recomposeReview(walletAddress: wallet, reviewId: reviewId);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Recomposing — a full writer loop takes a few minutes; it will replace this item when ready',
          ),
        ),
      );
      // The slot is freed immediately (the task completes the old review on
      // start), so reload quickly to show the queue emptied. The fresh draft then
      // arrives after the minutes-long writer loop — keep polling for it.
      await Future.delayed(const Duration(seconds: 2));
      if (!mounted) return;
      await _load();
      for (var i = 0; i < 20; i++) {
        // Stop once a newer item (not the one we recomposed) has appeared.
        final hasNew = _items.any((it) => it['review_id']?.toString() != reviewId);
        if (hasNew) break;
        await Future.delayed(const Duration(seconds: 15));
        if (!mounted) return;
        await _load();
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e')));
    } finally {
      if (mounted) setState(() => _recomposingId = null);
    }
  }

  Future<void> _clearQueue() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear review queue?'),
        content: Text(
          'Discards all ${_items.length} pending items without recording any '
          'feedback. Training labels you already submitted are kept.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(backgroundColor: Theme.of(ctx).colorScheme.error),
            child: const Text('Clear queue'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _loading = true);
    try {
      await ref.read(adminApiProvider).clearClassifierReviews(walletAddress: wallet);
      await _load();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Review queue cleared')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _loadTopBody() async {
    if (_items.isEmpty) {
      setState(() {
        _topBody = null;
        _topBodyForId = null;
      });
      return;
    }
    final articleId = _items.first['article_id']?.toString() ?? '';
    if (articleId.isEmpty) {
      setState(() {
        _topBody = null;
        _topBodyForId = null;
      });
      return;
    }
    if (_topBodyForId == articleId) return;
    try {
      final article = await NewsApi(ref.read(apiClientProvider)).fetchArticle(articleId);
      if (!mounted) return;
      final rawTags = article['tags'];
      setState(() {
        _topBody = article['body']?.toString() ?? '';
        _topTags = rawTags is List ? rawTags.map((e) => e.toString()).toList() : const [];
        _topBodyForId = articleId;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _topBody = null;
        _topBodyForId = articleId;
      });
    }
  }

  Future<void> _submit(
    Map<String, dynamic> item, {
    required bool approved,
  }) async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) return;
    final key = _itemKey(item);
    setState(() => _pending.add(key));
    try {
      final api = ref.read(adminApiProvider);
      final predictedCategory = (item['category'] as String? ?? 'generic').toLowerCase();
      // Send only dimensions the reviewer actually changed (vs the auto-score).
      final detail = item['grade_detail'];
      final subs = (detail is Map && detail['subscores'] is Map)
          ? Map<String, dynamic>.from(detail['subscores'] as Map)
          : <String, dynamic>{};
      final corrected = <String, double>{};
      _editedScores.forEach((dim, value) {
        final auto = ((subs[dim] as num?)?.toDouble() ?? 0) * 10;
        if ((value - auto).abs() >= 0.5) corrected[dim] = value;
      });
      await api.submitClassifierFeedback(
        walletAddress: wallet,
        url: item['url'] as String? ?? '',
        textSample: item['page_text_preview'] as String? ?? '',
        category: _categoriesFor(item).first,
        categories: _categoriesFor(item).toList(),
        predictedCategory: predictedCategory,
        quality: _qualityFor(item),
        sourceRelevant: _sourceRelevantFor(item),
        predictedPublish: false,
        approved: approved,
        trainingOnly: _trainingMode,
        correctedScores: corrected,
        anchor: _anchor[key] ?? false,
        factualityFail: _anchorFactFail[key] ?? false,
        toneFail: _anchorToneFail[key] ?? false,
        errorTypes: (_anchorTypes[key] ?? const <String>{}).toList(),
        reviewId: item['review_id'] as String?,
        articleId: item['article_id'] as String?,
      );
      _selectedCategories.remove(key);
      _selectedQuality.remove(key);
      _sourceRelevant.remove(key);
      _editedScores.clear();
      _anchor.remove(key);
      _anchorFactFail.remove(key);
      _anchorToneFail.remove(key);
      _anchorTypes.remove(key);
      await _load();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _pending.remove(key));
    }
  }

  /// One-time validation-anchor tagging: marks this graded item as ground truth
  /// for checking the LLM annotator. Collapsed until the reviewer opts in, so it
  /// never gets in the way of the normal pull-and-grade flow.
  Widget _anchorTagging(ThemeData theme, String key, bool busy) {
    final isAnchor = _anchor[key] ?? false;
    final types = _anchorTypes.putIfAbsent(key, () => <String>{});
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(Icons.verified_outlined, size: 18, color: theme.colorScheme.primary),
            const SizedBox(width: 6),
            Expanded(
              child: Text('Add to validation anchor set',
                  style: theme.textTheme.labelMedium),
            ),
            Switch(
              value: isAnchor,
              onChanged: busy ? null : (v) => setState(() => _anchor[key] = v),
            ),
          ],
        ),
        if (isAnchor) ...[
          const SizedBox(height: 4),
          Row(
            children: [
              Expanded(
                child: CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  controlAffinity: ListTileControlAffinity.leading,
                  title: const Text('Factuality fail'),
                  value: _anchorFactFail[key] ?? false,
                  onChanged: busy
                      ? null
                      : (v) => setState(() => _anchorFactFail[key] = v ?? false),
                ),
              ),
              Expanded(
                child: CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  controlAffinity: ListTileControlAffinity.leading,
                  title: const Text('Tone fail'),
                  value: _anchorToneFail[key] ?? false,
                  onChanged: busy
                      ? null
                      : (v) => setState(() => _anchorToneFail[key] = v ?? false),
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text('Error types', style: theme.textTheme.labelSmall),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: gatekeeperErrorTypes.map((t) {
              final selected = types.contains(t);
              return FilterChip(
                label: Text(gatekeeperErrorTypeLabel(t)),
                selected: selected,
                onSelected: busy
                    ? null
                    : (v) => setState(() {
                          if (v) {
                            types.add(t);
                          } else {
                            types.remove(t);
                          }
                        }),
              );
            }).toList(),
          ),
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return PageScroll(
      refresh: _load,
      children: [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          value: _trainingMode,
          onChanged: (v) => setState(() => _trainingMode = v),
          title: const Text('Training mode (label only — don’t publish)'),
          subtitle: const Text(
            'Accept/reject still trains both models, but accepted articles are NOT published. '
            'Use this for the bootstrap labelling sprint.',
          ),
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              child: Text(
                _items.isEmpty
                    ? 'Article proposals land here before publishing.'
                    : 'Article proposal 1 of ${_items.length} — approve to publish, '
                        'reject to discard. Sorted by interest score.',
                style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
              ),
            ),
            TextButton.icon(
              onPressed: (_loading || _composing) ? null : _composeNext,
              style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
              icon: _composing
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.auto_awesome_outlined, size: 20),
              label: const Text('Pull top topic'),
            ),
            if (_items.isNotEmpty)
              TextButton.icon(
                onPressed: _loading ? null : _clearQueue,
                style: TextButton.styleFrom(
                  foregroundColor: theme.colorScheme.error,
                  visualDensity: VisualDensity.compact,
                ),
                icon: const Icon(Icons.delete_sweep_outlined, size: 20),
                label: const Text('Clear queue'),
              ),
            IconButton(
              tooltip: 'Refresh',
              iconSize: 18,
              visualDensity: VisualDensity.compact,
              onPressed: _loading ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: AppLayout.itemGap),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        if (!_loading && _items.isEmpty)
          const EmptyState(
            title: 'Review queue is clear',
            message: 'Nothing is pending classifier review right now.',
            icon: Icons.rate_review_outlined,
          ),
        if (_items.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
            child: _reviewCard(theme, colors, _items.first),
          ),
        if (_items.length > 1)
          Center(
            child: Text(
              '${_items.length - 1} more waiting after this one',
              style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
            ),
          ),
      ],
    );
  }

  Widget _gradeBadge(ThemeData theme, dynamic colors, Map<String, dynamic> item) {
    final grade = (item['grade'] as num).toDouble();
    final color = grade >= 7
        ? const Color(0xFF2E7D32)
        : grade >= 5
            ? const Color(0xFFB7791F)
            : theme.colorScheme.error;
    final detail = item['grade_detail'];
    final subs = (detail is Map && detail['subscores'] is Map)
        ? Map<String, dynamic>.from(detail['subscores'] as Map)
        : <String, dynamic>{};
    final issues = (detail is Map && detail['issues'] is List)
        ? List<String>.from((detail['issues'] as List).map((e) => e.toString()))
        : <String>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 8,
          runSpacing: 6,
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: color.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: color.withValues(alpha: 0.5)),
              ),
              child: Text(
                'Grade ${grade.toStringAsFixed(1)}/10',
                style: theme.textTheme.labelMedium?.copyWith(color: color, fontWeight: FontWeight.w700),
              ),
            ),
            Text('drag a bar to correct a wrong score',
                style: theme.textTheme.labelSmall?.copyWith(color: colors.muted, fontStyle: FontStyle.italic)),
          ],
        ),
        const SizedBox(height: 6),
        ...subs.entries.map((e) {
          final auto = (e.value as num).toDouble() * 10; // 0-10
          final current = _editedScores[e.key] ?? auto;
          final edited = _editedScores.containsKey(e.key) && (current - auto).abs() >= 0.5;
          return Row(
            children: [
              SizedBox(
                width: 82,
                child: Text(e.key, style: theme.textTheme.labelSmall),
              ),
              Expanded(
                child: Slider(
                  value: current.clamp(0, 10),
                  min: 0,
                  max: 10,
                  divisions: 10,
                  label: current.round().toString(),
                  onChanged: (v) => setState(() => _editedScores[e.key] = v),
                ),
              ),
              SizedBox(
                width: 28,
                child: Text(
                  current.round().toString(),
                  textAlign: TextAlign.end,
                  style: theme.textTheme.labelSmall?.copyWith(
                    fontWeight: edited ? FontWeight.w700 : FontWeight.w400,
                    color: edited ? theme.colorScheme.primary : colors.muted,
                  ),
                ),
              ),
            ],
          );
        }),
        if (issues.isNotEmpty) ...[
          const SizedBox(height: 6),
          ...issues.map((i) => Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text('• $i',
                    style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.error)),
              )),
        ],
      ],
    );
  }

  Widget _reviewCard(ThemeData theme, dynamic colors, Map<String, dynamic> item) {
    final title = item['page_title'] as String? ?? item['url'] as String? ?? '';
    final url = item['url'] as String? ?? '';
    final predictedCategory = (item['category'] as String? ?? 'generic').toLowerCase();
    final score = item['storage_score'];
    final confidence = item['confidence'];
    final preview = item['page_text_preview'] as String? ?? '';
    final key = _itemKey(item);
    final busy = _pending.contains(key);
    final selectedCategories = _categoriesFor(item);
    final selectedQuality = _qualityFor(item);
    final sourceRelevant = _sourceRelevantFor(item);

    return Container(
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, maxLines: 2, overflow: TextOverflow.ellipsis, style: theme.textTheme.titleSmall),
          const SizedBox(height: 6),
          Text(
            url,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted, fontFamily: 'monospace', fontSize: 11),
          ),
          if (item['grade'] is num) ...[
            const SizedBox(height: 10),
            _gradeBadge(theme, colors, item),
          ],
          if ((item['article_title'] as String? ?? '').isNotEmpty) ...[
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: colors.calloutBackground,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Composed article (held — approve to publish)',
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.primary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    item['article_title'] as String? ?? '',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
                  ),
                  if ((item['article_summary'] as String? ?? '').isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(
                      item['article_summary'] as String? ?? '',
                      style: theme.textTheme.bodySmall?.copyWith(color: colors.muted, height: 1.4),
                    ),
                  ],
                  if (_topTags.isNotEmpty &&
                      _topBodyForId == (item['article_id']?.toString() ?? '')) ...[
                    const SizedBox(height: 10),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: _topTags
                          .map((t) => Container(
                                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                decoration: BoxDecoration(
                                  color: colors.accentSoft,
                                  borderRadius: BorderRadius.circular(6),
                                ),
                                child: Text(
                                  '#$t',
                                  style: theme.textTheme.labelSmall
                                      ?.copyWith(color: theme.colorScheme.primary),
                                ),
                              ))
                          .toList(),
                    ),
                  ],
                  if (_topBody != null &&
                      _topBodyForId == (item['article_id']?.toString() ?? '') &&
                      _topBody!.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    Container(
                      constraints: const BoxConstraints(maxHeight: 380),
                      decoration: BoxDecoration(
                        color: theme.cardTheme.color,
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: colors.border),
                      ),
                      padding: const EdgeInsets.all(14),
                      child: SingleChildScrollView(
                        child: ArticleMarkdown(data: _topBody!),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
          _EvidencePanel(url: url),
          if (preview.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              preview,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall?.copyWith(color: colors.muted, height: 1.5),
            ),
          ],
          const SizedBox(height: 14),
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: colors.accentSoft,
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  [
                    'Predicted: ${classifierCategoryLabel(predictedCategory)}',
                    if (score != null) 'score $score',
                    if (confidence is num)
                      'confidence ${(confidence * 100).toStringAsFixed(0)}%',
                  ].join(' · '),
                  style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.primary),
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Text('Categories (pick all that apply)', style: theme.textTheme.labelMedium),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: classifierCategories.map((cat) {
              final on = selectedCategories.contains(cat);
              return FilterChip(
                label: Text(classifierCategoryLabel(cat)),
                selected: on,
                showCheckmark: true,
                onSelected: busy
                    ? null
                    : (v) => setState(() {
                          final set = _categoriesFor(item);
                          if (v) {
                            set.add(cat);
                          } else if (set.length > 1) {
                            set.remove(cat);
                          }
                        }),
              );
            }).toList(),
          ),
          const SizedBox(height: 14),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: colors.calloutBackground,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Source worth watching?', style: theme.textTheme.labelMedium),
                      Text(
                        'Off = mark this domain a dead end. Rejecting a weak article alone keeps a good source alive.',
                        style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
                      ),
                    ],
                  ),
                ),
                Switch(
                  value: sourceRelevant,
                  onChanged: busy ? null : (v) => setState(() => _sourceRelevant[key] = v),
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          Text('Article quality', style: theme.textTheme.labelMedium),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: classifierQualityLevels.map((level) {
              final selected = selectedQuality == level;
              return ChoiceChip(
                label: Text(classifierQualityLabel(level)),
                selected: selected,
                onSelected: busy
                    ? null
                    : (_) => setState(() => _selectedQuality[key] = level),
              );
            }).toList(),
          ),
          const SizedBox(height: 14),
          _anchorTagging(theme, key, busy),
          const SizedBox(height: 16),
          Row(
            children: [
              Builder(builder: (context) {
                final recomposing = _recomposingId == (item['review_id']?.toString() ?? '');
                return TextButton.icon(
                  onPressed: (busy || _recomposingId != null)
                      ? null
                      : () => _recompose(item),
                  icon: recomposing
                      ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.refresh, size: 20),
                  label: const Text('Recompose'),
                  style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
                );
              }),
              const Spacer(),
              OutlinedButton.icon(
                onPressed: busy ? null : () => _submit(item, approved: false),
                icon: const Icon(Icons.close, size: 20),
                label: const Text('Reject'),
                style: OutlinedButton.styleFrom(foregroundColor: theme.colorScheme.error),
              ),
              const SizedBox(width: 8),
              FilledButton.tonalIcon(
                onPressed: busy ? null : () => _submit(item, approved: true),
                icon: busy
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.check, size: 20),
                label: const Text('Approve'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}


class _EvidencePanel extends ConsumerStatefulWidget {
  const _EvidencePanel({required this.url});
  final String url;

  @override
  ConsumerState<_EvidencePanel> createState() => _EvidencePanelState();
}

class _EvidencePanelState extends ConsumerState<_EvidencePanel> {
  List<Map<String, dynamic>>? _findings;
  bool _loading = false;

  Future<void> _load() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null) return;
    setState(() => _loading = true);
    try {
      final f = await ref.read(adminApiProvider)
          .investigationFindings(walletAddress: wallet, url: widget.url);
      if (!mounted) return;
      setState(() => _findings = f);
    } catch (_) {
      if (!mounted) return;
      setState(() => _findings = const []);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Theme(
        data: theme.copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: EdgeInsets.zero,
          childrenPadding: const EdgeInsets.only(bottom: 8),
          dense: true,
          leading: Icon(Icons.travel_explore_outlined, size: 18, color: colors.muted),
          title: Text('Investigation evidence', style: theme.textTheme.labelMedium),
          subtitle: Text(
            'Tools the agent called to verify this story',
            style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
          ),
          onExpansionChanged: (open) {
            if (open && _findings == null && !_loading) _load();
          },
          children: [
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(8),
                child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else if ((_findings ?? const []).isEmpty)
              Padding(
                padding: const EdgeInsets.all(8),
                child: Text('No tool calls recorded for this article.',
                    style: theme.textTheme.bodySmall?.copyWith(color: colors.muted)),
              )
            else
              ...(_findings ?? const []).map((f) => Container(
                    margin: const EdgeInsets.only(bottom: 6),
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: colors.calloutBackground,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(Icons.bolt_outlined, size: 20, color: theme.colorScheme.primary),
                            const SizedBox(width: 6),
                            Text(f['tool']?.toString() ?? '',
                                style: theme.textTheme.labelSmall?.copyWith(
                                    fontWeight: FontWeight.w700, color: theme.colorScheme.primary)),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(
                          _summarizeResult(f['result']),
                          style: theme.textTheme.bodySmall?.copyWith(color: colors.muted, height: 1.4),
                        ),
                      ],
                    ),
                  )),
          ],
        ),
      ),
    );
  }

  String _summarizeResult(dynamic result) {
    if (result is! Map) return result?.toString() ?? '';
    if (result['error'] != null) return 'error: ${result['error']}';
    final parts = <String>[];
    result.forEach((k, v) {
      if (v == null || v == '' || v == false) return;
      if (v is List && v.isEmpty) return;
      final vs = v is Map || v is List ? '${(v is List) ? v.length : ''} ${v.runtimeType}' : v.toString();
      parts.add('$k: $vs');
    });
    return parts.take(6).join(' · ');
  }
}
