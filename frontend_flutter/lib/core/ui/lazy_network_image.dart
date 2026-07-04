import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../config/app_config.dart';

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
  });

  final String url;
  final double? height;
  final double? width;
  final BoxFit fit;
  final Widget? placeholder;
  final Widget? error;

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
      errorBuilder: (context, error, stack) =>
          widget.error ?? const SizedBox.shrink(),
    );
  }
}
