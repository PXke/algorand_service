import 'package:flutter/material.dart';

import '../theme/app_theme_extension.dart';

/// Soft corner glows that give pages depth without distracting from content.
class AmbientBackground extends StatelessWidget {
  const AmbientBackground({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final size = MediaQuery.sizeOf(context);

    return Stack(
      fit: StackFit.expand,
      children: [
        Positioned(
          top: -size.height * 0.08,
          right: -size.width * 0.12,
          child: _GlowBlob(
            diameter: size.width * 0.45,
            color: colors.accent.withValues(alpha: 0.07),
          ),
        ),
        Positioned(
          bottom: -size.height * 0.06,
          left: -size.width * 0.18,
          child: _GlowBlob(
            diameter: size.width * 0.38,
            color: colors.heroGradientEnd.withValues(alpha: 0.06),
          ),
        ),
        child,
      ],
    );
  }
}

class _GlowBlob extends StatelessWidget {
  const _GlowBlob({required this.diameter, required this.color});

  final double diameter;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: Container(
        width: diameter,
        height: diameter,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: RadialGradient(
            colors: [color, color.withValues(alpha: 0)],
          ),
        ),
      ),
    );
  }
}
