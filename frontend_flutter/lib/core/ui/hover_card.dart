import 'package:flutter/material.dart';

import '../theme/app_theme_extension.dart';

/// Card surface that lifts gently on hover (web/desktop) with a soft
/// shadow and accent border, and ripples on tap everywhere.
class HoverCard extends StatefulWidget {
  const HoverCard({
    super.key,
    required this.child,
    this.onTap,
    this.color,
    this.borderRadius = 16,
    this.accentBorderOnHover = true,
  });

  final Widget child;
  final VoidCallback? onTap;
  final Color? color;
  final double borderRadius;
  final bool accentBorderOnHover;

  @override
  State<HoverCard> createState() => _HoverCardState();
}

class _HoverCardState extends State<HoverCard> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final radius = BorderRadius.circular(widget.borderRadius);
    final interactive = widget.onTap != null;
    final lifted = _hovered && interactive;

    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 160),
        curve: Curves.easeOut,
        transform: Matrix4.translationValues(0, lifted ? -2 : 0, 0),
        decoration: BoxDecoration(
          color: widget.color ?? colors.panelBackground,
          borderRadius: radius,
          border: Border.all(
            color: lifted && widget.accentBorderOnHover
                ? theme.colorScheme.primary.withValues(alpha: 0.45)
                : colors.border,
          ),
          boxShadow: [
            BoxShadow(
              color: lifted ? colors.cardHoverShadow : colors.cardShadow,
              blurRadius: lifted ? 18 : 6,
              offset: Offset(0, lifted ? 8 : 2),
            ),
          ],
        ),
        child: Material(
          color: Colors.transparent,
          borderRadius: radius,
          clipBehavior: Clip.antiAlias,
          child: InkWell(onTap: widget.onTap, child: widget.child),
        ),
      ),
    );
  }
}
