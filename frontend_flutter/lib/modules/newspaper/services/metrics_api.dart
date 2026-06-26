import '../../../core/api/api_client.dart';

class MetricsApi {
  MetricsApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> fetchPrice({String? assetId}) async {
    final query = assetId != null ? '?asset_id=$assetId' : '';
    return _client.getJson('/api/v1/metrics/price$query');
  }

  Future<Map<String, dynamic>> fetchDashboard({String? assetId}) async {
    final query = assetId != null ? '?asset_id=$assetId' : '';
    return _client.getJson('/api/v1/metrics/dashboard$query');
  }
}
