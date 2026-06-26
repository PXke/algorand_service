import 'package:flutter/material.dart';

/// Modern geometric monogram: a flat indigo tile with a diagonal facet and a
/// bold "P". Mirrors `web/favicon.svg`; distinct from the official Algorand mark.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 30});

  final double size;

  static const Color _base = Color(0xFF4338CA);
  static const Color _facet = Color(0xFF6366F1);

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return ClipRRect(
      borderRadius: BorderRadius.circular(size * 0.23),
      child: SizedBox(
        width: size,
        height: size,
        child: CustomPaint(
          painter: const _MonogramPainter(base: _base, facet: _facet),
          child: Center(
            child: Text(
              'P',
              style: theme.textTheme.titleMedium?.copyWith(
                color: Colors.white,
                fontWeight: FontWeight.w800,
                fontSize: size * 0.6,
                height: 1,
                letterSpacing: -0.5,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Solid tile with a single diagonal facet anchored at the top-right corner.
class _MonogramPainter extends CustomPainter {
  const _MonogramPainter({required this.base, required this.facet});

  final Color base;
  final Color facet;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    canvas.drawRect(Offset.zero & size, Paint()..color = base);
    final wedge = Path()
      ..moveTo(w * 0.46, 0)
      ..lineTo(w, 0)
      ..lineTo(w, h * 0.54)
      ..close();
    canvas.drawPath(wedge, Paint()..color = facet);
  }

  @override
  bool shouldRepaint(covariant _MonogramPainter oldDelegate) =>
      oldDelegate.base != base || oldDelegate.facet != facet;
}
