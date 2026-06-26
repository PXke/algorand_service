import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/api_providers.dart';
import '../services/metrics_api.dart';
import '../ui/metrics_dashboard_strip.dart';

/// Market metric tiles for the standing markets bar. Fetched once and shared
/// across pages; refreshes when invalidated.
final marketTilesProvider = FutureProvider<List<MetricTileData>>((ref) async {
  final client = ref.watch(apiClientProvider);
  final dashboard = await MetricsApi(client).fetchDashboard();
  final raw = dashboard['tiles'] as List<dynamic>? ?? const [];
  return raw
      .whereType<Map<String, dynamic>>()
      .map(
        (t) => MetricTileData(
          id: t['id']?.toString() ?? '',
          label: t['label']?.toString() ?? '',
          value: t['value']?.toString() ?? '—',
          hint: t['hint']?.toString(),
          available: t['available'] == true,
        ),
      )
      .toList();
});
