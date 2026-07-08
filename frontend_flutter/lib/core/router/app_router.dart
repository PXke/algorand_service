import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../config/app_config.dart';
import '../../core/deferred/deferred_load_pool.dart';
import '../../modules/admin/admin_entry.dart' deferred as admin;
import '../../modules/misc/misc_pages_entry.dart' deferred as misc;
import '../../modules/newspaper/newspaper_deferred_gate.dart';
import '../../modules/newspaper/ui/front_page.dart';
import '../../modules/shell/ui/app_shell.dart';
import '../../shared/widgets/deferred_widget.dart';
import 'pageview_beacon.dart';

/// Gentle fade-and-rise transition between products (native only; web is instant).
CustomTransitionPage<void> _page(GoRouterState state, Widget child) {
  if (kIsWeb) {
    return NoTransitionPage<void>(key: state.pageKey, child: child);
  }
  return CustomTransitionPage<void>(
    key: state.pageKey,
    child: child,
    transitionDuration: const Duration(milliseconds: 240),
    reverseTransitionDuration: const Duration(milliseconds: 160),
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      final curved = CurvedAnimation(parent: animation, curve: Curves.easeOutCubic);
      return FadeTransition(
        opacity: curved,
        child: SlideTransition(
          position: Tween<Offset>(
            begin: const Offset(0, 0.015),
            end: Offset.zero,
          ).animate(curved),
          child: child,
        ),
      );
    },
  );
}

GoRouter createAppRouter() {
  final beaconObserver = PageviewBeaconObserver();
  final router = GoRouter(
    initialLocation: '/',
    observers: [beaconObserver],
    routes: [
      ShellRoute(
        builder: (context, state, child) => AppShell(child: child),
        routes: [
          GoRoute(
            path: '/',
            pageBuilder: (context, state) => _page(state, const FrontPage()),
          ),
          GoRoute(
            path: '/news',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(
                () => loadDeferredWithRetry(loadNewspaperModule),
                buildNewsPage,
              ),
            ),
            routes: [
              GoRoute(
                path: 'articles/:articleId',
                pageBuilder: (context, state) => _page(
                  state,
                  DeferredWidget(
                    () => loadDeferredWithRetry(loadNewspaperModule),
                    () => buildArticleDetailPage(
                      articleId: state.pathParameters['articleId'] ?? '',
                    ),
                  ),
                ),
              ),
            ],
          ),
          GoRoute(
            path: '/section/:slug',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(
                () => loadDeferredWithRetry(loadNewspaperModule),
                () => buildSectionPage(slug: state.pathParameters['slug'] ?? ''),
              ),
            ),
          ),
          GoRoute(
            path: '/about',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(
                () => loadDeferredWithRetry(() => misc.loadLibrary()),
                () => misc.AboutPage(),
              ),
            ),
          ),
          GoRoute(
            path: '/contact',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(
                () => loadDeferredWithRetry(() => misc.loadLibrary()),
                () => misc.ContactPage(),
              ),
            ),
          ),
          GoRoute(
            path: '/suggestions',
            redirect: (context, state) =>
                AppConfig.instance.suggestionsEnabled ? null : '/',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(
                () => loadDeferredWithRetry(() => misc.loadLibrary()),
                () => misc.SuggestionsPage(),
              ),
            ),
          ),
          GoRoute(
            path: '/sources',
            redirect: (context, state) => '/admin',
          ),
          GoRoute(
            path: '/search',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(
                () => loadDeferredWithRetry(() => misc.loadLibrary()),
                () => misc.SearchPage(),
              ),
            ),
          ),
          GoRoute(
            path: '/admin',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(
                () => loadDeferredWithRetry(() => admin.loadLibrary()),
                () => admin.AdminPage(),
              ),
            ),
          ),
        ],
      ),
    ],
  );
  beaconObserver.attachRouter(router);
  return router;
}
