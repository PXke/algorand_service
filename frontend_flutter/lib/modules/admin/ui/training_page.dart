import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/api_providers.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../auth/providers/auth_providers.dart';

/// Admin tab: classifier/grader training data volume, balance, and readiness.
class TrainingTab extends ConsumerStatefulWidget {
  const TrainingTab({super.key});

  @override
  ConsumerState<TrainingTab> createState() => _TrainingTabState();
}

class _TrainingTabState extends ConsumerState<TrainingTab> {
  Map<String, dynamic> _stats = const {};
  bool _loading = true;
  String? _error;
  String? _retrainMsg;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
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
      final stats = await ref.read(adminApiProvider).getTrainingStats(walletAddress: wallet);
      if (!mounted) return;
      setState(() {
        _stats = stats;
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

  Future<void> _retrain() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) return;
    setState(() => _retrainMsg = 'Queuing retrain…');
    try {
      await ref.read(adminApiProvider).triggerRetrain(walletAddress: wallet);
      if (!mounted) return;
      setState(() => _retrainMsg = 'Retrain queued — refresh in a minute to see results.');
    } catch (e) {
      if (!mounted) return;
      setState(() => _retrainMsg = 'Retrain failed: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final total = (_stats['total_labeled'] as num?)?.toInt() ?? 0;
    final approved = (_stats['approved'] as num?)?.toInt() ?? 0;
    final rejected = (_stats['rejected'] as num?)?.toInt() ?? 0;
    final graded = (_stats['graded_trainable'] as num?)?.toInt() ?? 0;
    final gApproved = (_stats['graded_approved'] as num?)?.toInt() ?? 0;
    final gRejected = (_stats['graded_rejected'] as num?)?.toInt() ?? 0;
    final minSamples = (_stats['min_samples'] as num?)?.toInt() ?? 40;
    final ready = _stats['ready_to_train'] == true;

    return PageScroll(
      refresh: _load,
      children: [
        Text('Training data', style: theme.textTheme.titleMedium),
        const SizedBox(height: 4),
        Text(
          'Every accept/reject on the Review tab is a labelled example. The learned grader '
          'trains on rows that captured grade dimensions. Use "Training mode" on the Review '
          'tab to label without publishing.',
          style: theme.textTheme.bodySmall,
        ),
        const SizedBox(height: AppLayout.itemGap),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        _statRow(theme, 'Total labelled decisions', '$total'),
        _statRow(theme, 'Accept / reject balance', '$approved accept · $rejected reject'),
        const Divider(height: 24),
        _statRow(theme, 'Trainable (with grade dims)', '$graded'),
        _statRow(theme, '  └ accept / reject', '$gApproved / $gRejected'),
        _statRow(theme, 'Min samples to train', '$minSamples'),
        const SizedBox(height: 12),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: (ready ? const Color(0xFF2E7D32) : const Color(0xFFB7791F)).withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Text(
            ready
                ? '✓ Ready to train the learned grader ($graded balanced samples).'
                : 'Collecting… need $minSamples balanced graded samples (have $graded). '
                    'Until then the grader uses heuristic weights.',
            style: theme.textTheme.bodyMedium,
          ),
        ),
        const SizedBox(height: AppLayout.itemGap),
        Row(
          children: [
            FilledButton.icon(
              onPressed: _loading ? null : _retrain,
              icon: const Icon(Icons.model_training, size: 18),
              label: const Text('Retrain now'),
            ),
            const SizedBox(width: 12),
            if (_retrainMsg != null)
              Expanded(child: Text(_retrainMsg!, style: theme.textTheme.bodySmall)),
          ],
        ),
      ],
    );
  }

  Widget _statRow(ThemeData theme, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: theme.textTheme.bodyMedium),
          Text(value, style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}
