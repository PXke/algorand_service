import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../config/app_config.dart';

/// Art direction for LIST artwork (story-row thumbs, lead tile art): a mild
/// desaturation (s=0.8) so og-images scraped from dozens of unrelated brands
/// sit together as one page instead of shouting at each other. Article-BODY
/// images are never treated — the journalism shows its sources as they are.
const ColorFilter editorialThumbFilter = ColorFilter.matrix(<double>[
  0.84252, 0.14304, 0.01444, 0, 0,
  0.04252, 0.94304, 0.01444, 0, 0,
  0.04252, 0.14304, 0.81444, 0, 0,
  0, 0, 0, 1, 0,
]);

/// Network image that waits until the browser is idle before fetching — keeps
/// hero/card art off the engine-boot critical path.
class LazyNetworkImage extends StatefulWidget {
  const LazyNetworkImage({
    super.key,
    required this.url,
    this.height,
    this.width,
    this.fit = BoxFit.cover,
    this.placeholder,
    this.error,
    this.semanticLabel,
  });

  final String url;
  final double? height;
  final double? width;
  final BoxFit fit;
  final Widget? placeholder;
  final Widget? error;
  final String? semanticLabel;

  @override
  State<LazyNetworkImage> createState() => _LazyNetworkImageState();
}

class _LazyNetworkImageState extends State<LazyNetworkImage> {
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      SchedulerBinding.instance.scheduleTask(
        () {
          if (mounted) setState(() => _ready = true);
        },
        Priority.idle,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) {
      return widget.placeholder ??
          SizedBox(
            height: widget.height,
            width: widget.width,
          );
    }
    return Image.network(
      proxiedImageUrl(widget.url),
      height: widget.height,
      width: widget.width,
      fit: widget.fit,
      gaplessPlayback: true,
      semanticLabel: widget.semanticLabel,
      // Fade in on first paint instead of popping; synchronously-available
      // (cached) images render immediately.
      frameBuilder: (context, child, frame, wasSynchronouslyLoaded) {
        if (wasSynchronouslyLoaded) return child;
        return AnimatedOpacity(
          opacity: frame == null ? 0 : 1,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
          child: child,
        );
      },
      errorBuilder: (context, error, stack) =>
          widget.error ?? const SizedBox.shrink(),
    );
  }
}
