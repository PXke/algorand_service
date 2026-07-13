/// Bugsnag client configuration (`--dart-define` / deploy `FRONTEND_BUGSNAG_*`).
class BugsnagConfig {
  const BugsnagConfig._();

  static const apiKey = String.fromEnvironment(
    'BUGSNAG_API_KEY',
    defaultValue: '7712dd9a5b49cc654fd24ce23a18d0c3',
  );

  static const releaseStage = String.fromEnvironment(
    'BUGSNAG_RELEASE_STAGE',
    defaultValue: 'production',
  );

  static bool get enabled => apiKey.isNotEmpty;
}
