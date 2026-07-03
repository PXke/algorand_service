import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../config/app_config.dart';
// Admin is a heavy, rarely-visited section — load it as a deferred chunk so it
// stays out of the initial bundle. See DeferredWidget below.
import '../../modules/admin/ui/admin_page.dart' deferred as admin;
import '../../shared/widgets/deferred_widget.dart';
import '../../modules/newspaper/ui/about_page.dart';
import '../../modules/newspaper/ui/article_detail_page.dart';
import '../../modules/newspaper/ui/front_page.dart';
import '../../modules/newspaper/ui/news_page.dart';
import '../../modules/newspaper/ui/section_page.dart';
import '../../modules/search/ui/search_page.dart';
import '../../modules/shell/ui/app_shell.dart';
import '../../modules/suggestions/ui/suggestions_page.dart';
import 'pageview_beacon.dart';

/// Gentle fade-and-rise transition between products.
CustomTransitionPage<void> _page(GoRouterState state, Widget child) {
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
            pageBuilder: (context, state) => _page(state, const NewsPage()),
            routes: [
              GoRoute(
                path: 'articles/:articleId',
                pageBuilder: (context, state) => _page(
                  state,
                  ArticleDetailPage(
                    articleId: state.pathParameters['articleId'] ?? '',
                  ),
                ),
              ),
            ],
          ),
          GoRoute(
            path: '/section/:slug',
            pageBuilder: (context, state) => _page(
              state,
              SectionPage(slug: state.pathParameters['slug'] ?? ''),
            ),
          ),
          GoRoute(
            path: '/about',
            pageBuilder: (context, state) => _page(state, const AboutPage()),
          ),
          GoRoute(
            path: '/suggestions',
            redirect: (context, state) =>
                AppConfig.instance.suggestionsEnabled ? null : '/',
            pageBuilder: (context, state) => _page(state, const SuggestionsPage()),
          ),
          GoRoute(
            path: '/sources',
            redirect: (context, state) => '/admin',
          ),
          GoRoute(
            path: '/search',
            pageBuilder: (context, state) => _page(state, const SearchPage()),
          ),
          GoRoute(
            path: '/admin',
            pageBuilder: (context, state) => _page(
              state,
              DeferredWidget(admin.loadLibrary, () => admin.AdminPage()),
            ),
          ),
        ],
      ),
    ],
  );
  beaconObserver.attachRouter(router);
  return router;
}
