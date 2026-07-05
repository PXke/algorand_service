import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter/services.dart';

/// Loads non-critical font weights/families after first paint on web so
/// ~450 KiB of TTF data stays off the WASM boot path.
class DeferredFontLoader extends StatefulWidget {
  const DeferredFontLoader({super.key, required this.child});

  final Widget child;

  @override
  State<DeferredFontLoader> createState() => _DeferredFontLoaderState();
}

class _DeferredFontLoaderState extends State<DeferredFontLoader> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (kIsWeb) {
        SchedulerBinding.instance.scheduleTask(_loadDeferredFonts, Priority.idle);
      } else {
        unawaited(_loadDeferredFonts());
      }
    });
  }

  Future<void> _loadDeferredFonts() async {
    try {
      final serif = FontLoader('Source Serif 4')
        ..addFont(rootBundle.load('assets/fonts/SourceSerif4-w700.ttf'));
      final interBold = FontLoader('Inter')
        ..addFont(rootBundle.load('assets/fonts/Inter-w700.ttf'));
      await Future.wait([serif.load(), interBold.load()]);
      if (mounted) setState(() {});
    } catch (_) {
      // Headlines fall back to Inter until fonts are available.
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
