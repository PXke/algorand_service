// Public API for the server-side analytics opt-out. The implementation is
// web-only (sets a cookie the SSR pageview recorder checks); on other platforms
// it's a no-op. Conditional import keeps mobile/desktop builds compiling.
import 'analytics_opt_out_stub.dart'
    if (dart.library.html) 'analytics_opt_out_web.dart' as impl;

/// When [enabled], set a cookie that tells the backend to skip recording this
/// browser's pageviews (used to exclude the admin/owner's own visits).
void setAnalyticsOptOut(bool enabled) => impl.setAnalyticsOptOut(enabled);
