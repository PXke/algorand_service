import 'api_client.dart';

/// User-facing message from an API or unexpected error.
String apiErrorMessage(Object error, {String? fallback}) {
  if (error is ApiException) {
    if (error.message.isNotEmpty && error.message != 'Request failed') {
      return error.message;
    }
    return switch (error.code) {
      'network_error' => error.message,
      'invalid_response' => error.message,
      'not_found' => 'Not found',
      _ => error.code.replaceAll('_', ' '),
    };
  }
  return fallback ?? 'Something went wrong. Try again.';
}
