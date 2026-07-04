import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/providers/session_providers.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/format.dart';
import '../../../core/ui/info_callout.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/hover_card.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/ui/page_header.dart';
import '../services/suggestions_api.dart';

class SuggestionsPage extends ConsumerStatefulWidget {
  const SuggestionsPage({super.key});

  @override
  ConsumerState<SuggestionsPage> createState() => _SuggestionsPageState();
}

class _SuggestionsPageState extends ConsumerState<SuggestionsPage> {
  List<Map<String, dynamic>> _items = const [];
  String? _error;
  bool _loading = true;
  String? _treasuryAddress;
  String? _minAlgoDisplay;

  final _titleController = TextEditingController();
  final _bodyController = TextEditingController();
  final _txidController = TextEditingController();
  final _signatureController = TextEditingController();
  String? _upvoteSuggestionId;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _titleController.dispose();
    _bodyController.dispose();
    _txidController.dispose();
    _signatureController.dispose();
    super.dispose();
  }

  SuggestionsApi _api() {
    final client = ref.read(apiClientProvider);
    final headers = ref.read(sessionHeadersProvider);
    return SuggestionsApi(client, headers: headers);
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final config = await _api().fetchConfig();
      final items = await _api().listOpen();
      if (!mounted) return;
      setState(() {
        _treasuryAddress = config['treasury_address']?.toString();
        _minAlgoDisplay = config['min_algo_display']?.toString();
        _items = items;
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

  Future<void> _submit() async {
    final l10n = context.l10n;
    final headers = ref.read(sessionHeadersProvider);
    if (headers.isEmpty) {
      _showSnack(l10n.snackConnectWallet);
      return;
    }
    try {
      await _api().create(
        title: _titleController.text.trim(),
        body: _bodyController.text.trim(),
        submissionTxid: _txidController.text.trim(),
      );
      _titleController.clear();
      _bodyController.clear();
      _txidController.clear();
      await _refresh();
      if (!mounted) return;
      _showSnack(l10n.snackSuggestionSubmitted);
    } catch (e) {
      _showSnack(apiErrorMessage(e));
    }
  }

  Future<void> _prepareUpvote(String suggestionId) async {
    final l10n = context.l10n;
    final headers = ref.read(sessionHeadersProvider);
    if (headers.isEmpty) {
      _showSnack(l10n.snackConnectWallet);
      return;
    }
    try {
      final messageBody = await _api().upvoteMessage(suggestionId);
      final signingMessage = messageBody['message']?.toString() ?? '';
      setState(() {
        _upvoteSuggestionId = suggestionId;
        _signatureController.text = '';
      });
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(l10n.suggestionsUpvoteDialogTitle),
          content: SelectableText(signingMessage),
          actions: [
            TextButton(
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: signingMessage));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text(l10n.suggestionsMessageCopied)),
                  );
                }
              },
              child: Text(l10n.suggestionsCopyMessage),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: Text(l10n.close),
            ),
          ],
        ),
      );
    } catch (e) {
      _showSnack(apiErrorMessage(e));
    }
  }

  Future<void> _submitUpvote() async {
    final l10n = context.l10n;
    final suggestionId = _upvoteSuggestionId;
    if (suggestionId == null) {
      _showSnack(l10n.snackChooseSuggestionUpvote);
      return;
    }
    try {
      await _api().upvote(
        suggestionId: suggestionId,
        signatureB64: _signatureController.text.trim(),
      );
      await _refresh();
      if (!mounted) return;
      _showSnack(l10n.snackUpvoteRecorded);
    } catch (e) {
      _showSnack(apiErrorMessage(e));
    }
  }

  void _showSnack(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final treasury = _treasuryAddress;
    final minAlgo = _minAlgoDisplay ?? '0.01';

    return PageScroll(
      children: [
        PageHeader(
          title: l10n.suggestionsTitle,
          subtitle: l10n.suggestionsSubtitle,
        ),
        if (treasury != null && treasury.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: AppLayout.sectionGap),
            child: InfoCallout(
              message: l10n.suggestionsTreasuryHelp(minAlgo, treasury),
            ),
          ),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        ..._items.map((item) => Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _suggestionCard(context, theme, l10n, item),
            )),
        const SizedBox(height: AppLayout.sectionGap),
        _FormSection(
          title: l10n.suggestionsNewTitle,
          children: [
            TextField(
              controller: _titleController,
              decoration: InputDecoration(labelText: l10n.suggestionsFieldTitle),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _bodyController,
              minLines: 3,
              maxLines: 6,
              decoration: InputDecoration(labelText: l10n.suggestionsFieldBody),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _txidController,
              decoration: InputDecoration(labelText: l10n.suggestionsFieldTxid),
            ),
            const SizedBox(height: 16),
            FilledButton(onPressed: _submit, child: Text(l10n.suggestionsSubmit)),
          ],
        ),
        const SizedBox(height: AppLayout.sectionGap),
        _FormSection(
          title: l10n.suggestionsUpvoteTitle,
          children: [
            TextField(
              controller: _signatureController,
              decoration: InputDecoration(
                labelText: l10n.suggestionsSignatureLabel,
                hintText: l10n.suggestionsSignatureHint,
              ),
            ),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: _submitUpvote, child: Text(l10n.suggestionsSubmitUpvote)),
          ],
        ),
      ],
    );
  }

  Widget _suggestionCard(
    BuildContext context,
    ThemeData theme,
    AppLocalizations l10n,
    Map<String, dynamic> item,
  ) {
    final id = item['suggestion_id']?.toString() ?? '';
    final txid = item['submission_txid']?.toString() ?? '';
    final upvoteCount = item['upvote_count'] as int? ?? 0;

    final colors = context.appColors;

    return HoverCard(
      child: Padding(
        padding: const EdgeInsets.all(22),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: colors.accentSoft,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Icon(Icons.lightbulb_outline, size: 20, color: colors.accent),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Text(item['title']?.toString() ?? '', style: theme.textTheme.titleMedium),
                ),
              ],
            ),
            const SizedBox(height: 14),
            Text(item['body']?.toString() ?? '', style: theme.textTheme.bodyMedium),
            const SizedBox(height: 14),
            Text(
              l10n.suggestionsTxShort(truncateMiddle(txid, head: 10, tail: 8)),
              style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
            ),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: colors.accentSoft,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                l10n.suggestionsUpvoteCount(upvoteCount),
                style: theme.textTheme.labelMedium?.copyWith(color: colors.accent),
              ),
            ),
            const SizedBox(height: 16),
            OutlinedButton.icon(
              onPressed: id.isEmpty ? null : () => _prepareUpvote(id),
              icon: const Icon(Icons.how_to_vote_outlined, size: 18),
              label: Text(l10n.suggestionsPrepareUpvote),
            ),
          ],
        ),
      ),
    );
  }
}

class _FormSection extends StatelessWidget {
  const _FormSection({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final theme = Theme.of(context);

    return PageContent(
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: colors.panelBackground,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: colors.border),
          boxShadow: [
            BoxShadow(
              color: colors.cardShadow,
              blurRadius: 10,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(title, style: theme.textTheme.titleMedium),
              const SizedBox(height: 4),
              Container(
                width: 32,
                height: 2,
                margin: const EdgeInsets.only(bottom: 18),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primary.withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(1),
                ),
              ),
              ...children,
            ],
          ),
        ),
      ),
    );
  }
}
