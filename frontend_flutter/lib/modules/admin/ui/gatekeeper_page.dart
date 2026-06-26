import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/page_content.dart';
import '../../auth/providers/auth_providers.dart';
import '../models/classifier_labels.dart';

/// Gatekeeper validation tab: anchor progress (X/40), one-click annotator
/// validation with its report, and tagging an already-published article into the
/// anchor set (so the set can be curated, not just whatever flows through review).
class GatekeeperTab extends ConsumerStatefulWidget {
  const GatekeeperTab({super.key});

  @override
  ConsumerState<GatekeeperTab> createState() => _GatekeeperTabState();
}

class _GatekeeperTabState extends ConsumerState<GatekeeperTab> {
  bool _loading = true;
  bool _running = false;
  String? _error;
  Map<String, dynamic> _anchors = const {'count': 0, 'target': 40, 'items': []};
  Map<String, dynamic>? _report; // the {computed_at, report: {...}} wrapper

  final _articleIdCtrl = TextEditingController();
  bool _newFactFail = false;
  bool _newToneFail = false;
  final Set<String> _newTypes = {};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    _articleIdCtrl.dispose();
    super.dispose();
  }

  String? get _wallet => ref.read(walletAuthStateProvider).walletAddress;

  Future<void> _load() async {
    final wallet = _wallet;
    if (wallet == null || wallet.isEmpty) return;
    setState(() => _loading = true);
    try {
      final api = ref.read(adminApiProvider);
      final anchors = await api.listGatekeeperAnchors(walletAddress: wallet);
      final report = await api.getGatekeeperValidationReport(walletAddress: wallet);
      if (!mounted) return;
      setState(() {
        _anchors = anchors;
        _report = (report['report'] == null) ? null : report;
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _runValidation() async {
    final wallet = _wallet;
    if (wallet == null || wallet.isEmpty) return;
    setState(() => _running = true);
    try {
      await ref.read(adminApiProvider).runGatekeeperValidation(walletAddress: wallet);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Validation queued — refreshing report shortly…')),
      );
      await Future<void>.delayed(const Duration(seconds: 8));
      await _load();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _running = false);
    }
  }

  Future<void> _addAnchor() async {
    final wallet = _wallet;
    if (wallet == null || wallet.isEmpty) return;
    final id = _articleIdCtrl.text.trim();
    if (id.isEmpty) return;
    try {
      await ref.read(adminApiProvider).addGatekeeperAnchor(
            walletAddress: wallet,
            articleId: id,
            factualityFail: _newFactFail,
            toneFail: _newToneFail,
            errorTypes: _newTypes.toList(),
          );
      _articleIdCtrl.clear();
      setState(() {
        _newFactFail = false;
        _newToneFail = false;
        _newTypes.clear();
      });
      await _load();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final count = (_anchors['count'] as num?)?.toInt() ?? 0;
    final target = (_anchors['target'] as num?)?.toInt() ?? 40;
    final items = (_anchors['items'] as List?) ?? const [];

    return PageScroll(
      refresh: _load,
      children: [
        if (_error != null) ...[ErrorBanner(message: _error!), const SizedBox(height: 12)],
        if (_loading) const LinearProgressIndicator(minHeight: 2),
        const SizedBox(height: 8),

        // --- Anchor progress ------------------------------------------------
        _card(theme, [
          Row(
            children: [
              Icon(Icons.verified_outlined, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Text('Validation anchors', style: theme.textTheme.titleMedium),
              const Spacer(),
              Text('$count / $target', style: theme.textTheme.titleMedium),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(
              value: target == 0 ? 0 : (count / target).clamp(0.0, 1.0),
              minHeight: 8,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Tagged anchors are immutable ground truth used only to validate the '
            'LLM annotator — never to train. Aim for a diverse set (clean, '
            'hallucinated, hyped). Reach ~$target, then run validation.',
            style: theme.textTheme.bodySmall,
          ),
        ]),
        const SizedBox(height: 12),

        // --- Run validation -------------------------------------------------
        _card(theme, [
          Row(
            children: [
              Text('Annotator validation', style: theme.textTheme.titleMedium),
              const Spacer(),
              FilledButton.tonalIcon(
                onPressed: _running ? null : _runValidation,
                icon: _running
                    ? const SizedBox(
                        width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.play_arrow, size: 20),
                label: const Text('Run validation'),
              ),
            ],
          ),
          const SizedBox(height: 8),
          _reportBody(theme),
        ]),
        const SizedBox(height: 12),

        // --- Add published article as anchor --------------------------------
        _card(theme, [
          Text('Tag a published article as anchor', style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _articleIdCtrl,
            decoration: const InputDecoration(
              labelText: 'Article ID',
              isDense: true,
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  controlAffinity: ListTileControlAffinity.leading,
                  title: const Text('Factuality fail'),
                  value: _newFactFail,
                  onChanged: (v) => setState(() => _newFactFail = v ?? false),
                ),
              ),
              Expanded(
                child: CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  controlAffinity: ListTileControlAffinity.leading,
                  title: const Text('Tone fail'),
                  value: _newToneFail,
                  onChanged: (v) => setState(() => _newToneFail = v ?? false),
                ),
              ),
            ],
          ),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: gatekeeperErrorTypes.map((t) {
              return FilterChip(
                label: Text(gatekeeperErrorTypeLabel(t)),
                selected: _newTypes.contains(t),
                onSelected: (v) => setState(() {
                  if (v) {
                    _newTypes.add(t);
                  } else {
                    _newTypes.remove(t);
                  }
                }),
              );
            }).toList(),
          ),
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerRight,
            child: OutlinedButton.icon(
              onPressed: _addAnchor,
              icon: const Icon(Icons.add, size: 18),
              label: const Text('Add anchor'),
            ),
          ),
        ]),
        const SizedBox(height: 12),

        // --- Anchor list ----------------------------------------------------
        _card(theme, [
          Text('Tagged anchors (${items.length})', style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          if (items.isEmpty)
            Text('No anchors yet.', style: theme.textTheme.bodySmall)
          else
            ...items.whereType<Map<String, dynamic>>().map((a) => _anchorRow(theme, a)),
        ]),
      ],
    );
  }

  Widget _reportBody(ThemeData theme) {
    final wrapper = _report;
    if (wrapper == null) {
      return Text('Not run yet. Tag anchors, then press Run validation.',
          style: theme.textTheme.bodySmall);
    }
    final report = (wrapper['report'] as Map?)?.cast<String, dynamic>() ?? const {};
    final gated = report['gated'] == true;
    final n = (report['n_anchors'] as num?)?.toInt() ?? 0;
    final factAgree = (report['factuality_agreement'] as num?)?.toDouble() ?? 0;
    final toneAgree = (report['tone_agreement'] as num?)?.toDouble() ?? 0;
    final trusted = (report['trusted_types'] as List?)?.cast<String>() ?? const [];
    final perType = (report['per_type'] as Map?)?.cast<String, dynamic>() ?? const {};

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Computed: ${wrapper['computed_at'] ?? '—'}  ·  anchors: $n',
            style: theme.textTheme.bodySmall),
        if (gated)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text('⚠ Too few anchors for a reliable report (need ≥20). Trust nothing yet.',
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error)),
          ),
        const SizedBox(height: 6),
        Text('Fail-flag agreement — factuality ${(factAgree * 100).toStringAsFixed(0)}%, '
            'tone ${(toneAgree * 100).toStringAsFixed(0)}%',
            style: theme.textTheme.bodyMedium),
        const SizedBox(height: 6),
        Text('Trusted error types: ${trusted.isEmpty ? "none" : trusted.join(", ")}',
            style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        ...perType.entries.map((e) {
          final m = (e.value as Map?)?.cast<String, dynamic>() ?? const {};
          final p = (m['precision'] as num?)?.toDouble() ?? 0;
          final r = (m['recall'] as num?)?.toDouble() ?? 0;
          final sup = (m['support'] as num?)?.toInt() ?? 0;
          final ok = m['trusted'] == true;
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 2),
            child: Row(
              children: [
                Icon(ok ? Icons.check_circle : Icons.remove_circle_outline,
                    size: 16,
                    color: ok ? Colors.green : theme.colorScheme.outline),
                const SizedBox(width: 6),
                Expanded(child: Text(gatekeeperErrorTypeLabel(e.key))),
                Text('P ${p.toStringAsFixed(2)}  R ${r.toStringAsFixed(2)}  n=$sup',
                    style: theme.textTheme.bodySmall),
              ],
            ),
          );
        }),
      ],
    );
  }

  Widget _anchorRow(ThemeData theme, Map<String, dynamic> a) {
    final types = (a['error_types'] as List?)?.cast<String>() ?? const [];
    final flags = <String>[
      if (a['factuality_fail'] == true) 'factuality',
      if (a['tone_fail'] == true) 'tone',
    ];
    final label = flags.isEmpty ? 'clean' : flags.join(' + ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Expanded(
            child: Text(
              (a['article_id'] as String?)?.isNotEmpty == true
                  ? a['article_id'] as String
                  : (a['url'] as String? ?? '—'),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall,
            ),
          ),
          const SizedBox(width: 8),
          Text(
            types.isEmpty ? label : '$label · ${types.join(", ")}',
            style: theme.textTheme.bodySmall?.copyWith(color: context.appColors.muted),
          ),
        ],
      ),
    );
  }

  Widget _card(ThemeData theme, List<Widget> children) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
      ),
    );
  }
}
