import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/meta_row.dart';
import '../../../core/providers/admin_provider.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/ui/page_header.dart';
import '../../auth/providers/auth_providers.dart';
import '../models/source_kind.dart';
import '../services/registry_api.dart';
import 'source_kind_chip.dart';

class SourcesPage extends ConsumerStatefulWidget {
  const SourcesPage({super.key});

  @override
  ConsumerState<SourcesPage> createState() => _SourcesPageState();
}

class _SourcesPageState extends ConsumerState<SourcesPage> {
  List<Map<String, dynamic>> _items = const [];
  String? _error;
  bool _loading = true;
  SourceKind? _filterKind;

  RegistryApi _api() => RegistryApi(ref.read(apiClientProvider));

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await _api().fetchServices();
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _openEditor(Map<String, dynamic>? existing) async {
    final changed = await showDialog<bool>(
      context: context,
      builder: (_) => SourceEditDialog(existing: existing),
    );
    if (changed == true) {
      await _load();
    }
  }

  List<Map<String, dynamic>> get _seeds =>
      _items.where((i) => (i['origin']?.toString() ?? 'seed') != 'domain').toList();

  List<Map<String, dynamic>> get _visible {
    final base = _seeds;
    if (_filterKind == null) {
      return base;
    }
    return base.where((item) {
      return SourceKind.fromApi(item['source_kind']?.toString()) == _filterKind;
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final counts = {
      SourceKind.discord: _seeds.where((i) => i['source_kind'] == 'discord').length,
      SourceKind.reddit: _seeds.where((i) => i['source_kind'] == 'reddit').length,
      SourceKind.web: _seeds.where((i) => i['source_kind'] == 'web').length,
    };

    return PageScroll(
      refresh: _load,
      children: [
        PageHeader(
          title: l10n.seedsTitle,
          subtitle: l10n.seedsSubtitle,
          trailing: ref.watch(isAdminWalletProvider)
              ? FilledButton.icon(
                  onPressed: () => _openEditor(null),
                  icon: const Icon(Icons.add, size: 18),
                  label: Text(l10n.sourcesAdd),
                )
              : null,
        ),
        _FilterBar(
          l10n: l10n,
          total: _items.length,
          counts: counts,
          selected: _filterKind,
          onSelected: (kind) => setState(() => _filterKind = kind),
        ),
        const SizedBox(height: AppLayout.sectionGap),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        if (!_loading && _visible.isEmpty)
          EmptyState(
            title: l10n.sourcesEmptyTitle,
            message: l10n.sourcesEmptyMessage,
            icon: Icons.hub_outlined,
          ),
        ..._visible.map((item) => Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _sourceCard(context, theme, l10n, item),
            )),
      ],
    );
  }

  Widget _sourceCard(
    BuildContext context,
    ThemeData theme,
    AppLocalizations l10n,
    Map<String, dynamic> item,
  ) {
    final serviceId = item['service_id']?.toString() ?? '';
    final displayName = item['display_name']?.toString() ?? serviceId;
    final scrapeUrl = item['scrape_url']?.toString();
    final matchKind = item['match_kind']?.toString() ?? '';
    final matchValue = item['match_value']?.toString() ?? '';
    final enabled = item['enabled'] == true;
    final kind = SourceKind.fromApi(item['source_kind']?.toString());

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(child: Text(displayName, style: theme.textTheme.titleMedium)),
                SourceKindChip(kind: kind),
                if (!enabled) ...[
                  const SizedBox(width: 8),
                  _StatusLabel(text: l10n.sourcesDisabled),
                ],
              ],
            ),
            const SizedBox(height: 16),
            MetaRow(label: l10n.sourcesMetaServiceId, value: serviceId),
            if (scrapeUrl != null && scrapeUrl.isNotEmpty)
              MetaRow(label: l10n.sourcesMetaScrapeUrl, value: scrapeUrl),
            MetaRow(
              label: l10n.sourcesMetaMatchRule,
              value: l10n.matchRuleValue(matchKind, matchValue),
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                if (ref.watch(isAdminWalletProvider)) ...[
                  OutlinedButton.icon(
                    onPressed: () => _openEditor(item),
                    icon: const Icon(Icons.edit_outlined, size: 18),
                    label: Text(l10n.sourcesEdit),
                  ),
                  const SizedBox(width: 8),
                ],
                OutlinedButton.icon(
                  onPressed: serviceId.isEmpty
                      ? null
                      : () => context.go('/news?service_id=$serviceId'),
                  icon: const Icon(Icons.article_outlined, size: 18),
                  label: Text(l10n.sourcesViewArticles),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _FilterBar extends StatelessWidget {
  const _FilterBar({
    required this.l10n,
    required this.total,
    required this.counts,
    required this.selected,
    required this.onSelected,
  });

  final AppLocalizations l10n;
  final int total;
  final Map<SourceKind, int> counts;
  final SourceKind? selected;
  final void Function(SourceKind?) onSelected;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        _filterChip(
          label: l10n.filterAll(total),
          selected: selected == null,
          onTap: () => onSelected(null),
        ),
        if (counts[SourceKind.discord]! > 0)
          _filterChip(
            label: l10n.filterDiscord(counts[SourceKind.discord]!),
            selected: selected == SourceKind.discord,
            onTap: () => onSelected(selected == SourceKind.discord ? null : SourceKind.discord),
          ),
        if (counts[SourceKind.reddit]! > 0)
          _filterChip(
            label: l10n.filterReddit(counts[SourceKind.reddit]!),
            selected: selected == SourceKind.reddit,
            onTap: () => onSelected(selected == SourceKind.reddit ? null : SourceKind.reddit),
          ),
        if (counts[SourceKind.web]! > 0)
          _filterChip(
            label: l10n.filterWeb(counts[SourceKind.web]!),
            selected: selected == SourceKind.web,
            onTap: () => onSelected(selected == SourceKind.web ? null : SourceKind.web),
          ),
      ],
    );
  }

  Widget _filterChip({
    required String label,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return FilterChip(
      label: Text(label),
      selected: selected,
      showCheckmark: true,
      onSelected: (_) => onTap(),
    );
  }
}

class _StatusLabel extends StatelessWidget {
  const _StatusLabel({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: colors.calloutBackground,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: colors.border),
      ),
      child: Text(
        text,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(
              color: colors.muted,
              fontWeight: FontWeight.w600,
            ),
      ),
    );
  }
}

/// Admin dialog: create or edit a service-registry source.
class SourceEditDialog extends ConsumerStatefulWidget {
  const SourceEditDialog({super.key, this.existing});

  /// Registry item being edited, or null to create a new one.
  final Map<String, dynamic>? existing;

  @override
  ConsumerState<SourceEditDialog> createState() => _SourceEditDialogState();
}

class _SourceEditDialogState extends ConsumerState<SourceEditDialog> {
  late final TextEditingController _idController;
  late final TextEditingController _nameController;
  late final TextEditingController _urlController;
  late final TextEditingController _matchKindController;
  late final TextEditingController _matchValueController;
  late bool _enabled;
  bool _busy = false;
  String? _error;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _idController = TextEditingController(text: e?['service_id']?.toString() ?? '');
    _nameController = TextEditingController(text: e?['display_name']?.toString() ?? '');
    _urlController = TextEditingController(text: e?['scrape_url']?.toString() ?? '');
    _matchKindController = TextEditingController(text: e?['match_kind']?.toString() ?? 'domain');
    _matchValueController = TextEditingController(text: e?['match_value']?.toString() ?? '');
    _enabled = e == null || e['enabled'] == true;
  }

  @override
  void dispose() {
    _idController.dispose();
    _nameController.dispose();
    _urlController.dispose();
    _matchKindController.dispose();
    _matchValueController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null) return;
    final id = _idController.text.trim();
    final name = _nameController.text.trim();
    final url = _urlController.text.trim();
    if (id.isEmpty || name.isEmpty || url.isEmpty) {
      setState(() => _error = context.l10n.sourcesRequiredFields);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(adminApiProvider).upsertSource(
        walletAddress: wallet,
        serviceId: id,
        displayName: name,
        scrapeUrl: url,
        matchKind: _matchKindController.text.trim(),
        matchValue: _matchValueController.text.trim(),
        enabled: _enabled,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _busy = false;
      });
    }
  }

  Future<void> _delete() async {
    final l10n = context.l10n;
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    final id = widget.existing?['service_id']?.toString();
    if (wallet == null || id == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.sourcesDeleteTitle),
        content: Text(l10n.sourcesDeleteBody(id)),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(l10n.actionCancel),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(ctx).colorScheme.error,
            ),
            child: Text(l10n.sourcesDelete),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(adminApiProvider).deleteSource(
        walletAddress: wallet,
        serviceId: id,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final l10n = context.l10n;

    return AlertDialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      title: Text(_isEdit ? l10n.sourcesEditTitle : l10n.sourcesAddTitle),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _idController,
                enabled: !_isEdit,
                decoration: InputDecoration(
                  labelText: l10n.sourcesFieldServiceId,
                  hintText: l10n.sourcesFieldServiceIdHint,
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _nameController,
                decoration: InputDecoration(labelText: l10n.sourcesFieldDisplayName),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _urlController,
                decoration: InputDecoration(
                  labelText: l10n.sourcesFieldScrapeUrl,
                  hintText: l10n.sourcesFieldScrapeUrlHint,
                ),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _matchKindController,
                      decoration: InputDecoration(
                        labelText: l10n.sourcesFieldMatchKind,
                        hintText: l10n.sourcesFieldMatchKindHint,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: TextField(
                      controller: _matchValueController,
                      decoration: InputDecoration(
                        labelText: l10n.sourcesFieldMatchValue,
                        hintText: l10n.sourcesFieldMatchValueHint,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                l10n.sourcesMatchRuleHelp,
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                      color: context.appColors.muted,
                      height: 1.4,
                    ),
              ),
              const SizedBox(height: 8),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.sourcesEnabled, style: theme.textTheme.bodyMedium),
                value: _enabled,
                onChanged: (v) => setState(() => _enabled = v),
              ),
              if (_error != null) ...[
                const SizedBox(height: 4),
                Text(
                  _error!,
                  style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                ),
              ],
              if (_isEdit) ...[
                const SizedBox(height: 4),
                Text(
                  l10n.sourcesChangesNextPoll,
                  style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
                ),
              ],
            ],
          ),
        ),
      ),
      actions: [
        if (_isEdit)
          TextButton(
            onPressed: _busy ? null : _delete,
            style: TextButton.styleFrom(foregroundColor: theme.colorScheme.error),
            child: Text(l10n.sourcesDelete),
          ),
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(false),
          child: Text(l10n.actionCancel),
        ),
        FilledButton(
          onPressed: _busy ? null : _save,
          child: _busy
              ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
              : Text(_isEdit ? l10n.sourcesSave : l10n.sourcesAddAction),
        ),
      ],
    );
  }
}
