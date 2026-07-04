// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
// Web implementation: a first-party cookie the SSR pageview recorder checks
// (see backend seo/api/routes.py::_record). Set when the admin wallet is
// connected so the owner's own visits aren't counted as traffic.
import 'dart:html' as html;

const _name = 'pxke_no_track';

void setAnalyticsOptOut(bool enabled) {
  final host = html.window.location.hostname ?? '';
  // Share the opt-out cookie with the API subdomain (algorand-api.pxke.me).
  final domain =
      host == 'pxke.me' || host.endsWith('.pxke.me') ? '; domain=.pxke.me' : '';
  html.document.cookie = enabled
      ? '$_name=1; path=/$domain; max-age=31536000; SameSite=Lax'
      : '$_name=; path=/$domain; max-age=0; SameSite=Lax';
}
