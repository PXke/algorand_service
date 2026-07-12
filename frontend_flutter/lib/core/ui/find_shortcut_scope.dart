import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../util/find_shortcut.dart';

class _OpenSearchIntent extends Intent {
  const _OpenSearchIntent();
}

/// Intercepts Ctrl/Cmd+F and opens site search (Flutter canvas text is not
/// searchable with the browser's native find).
class FindShortcutScope extends StatefulWidget {
  const FindShortcutScope({super.key, required this.child});

  final Widget child;

  @override
  State<FindShortcutScope> createState() => _FindShortcutScopeState();
}

class _FindShortcutScopeState extends State<FindShortcutScope> {
  @override
  void initState() {
    super.initState();
    if (kIsWeb) {
      installWebFindShortcut(_openSearch);
    } else {
      HardwareKeyboard.instance.addHandler(_onKey);
    }
  }

  @override
  void dispose() {
    if (kIsWeb) {
      uninstallWebFindShortcut();
    } else {
      HardwareKeyboard.instance.removeHandler(_onKey);
    }
    super.dispose();
  }

  bool _onKey(KeyEvent event) {
    if (event is! KeyDownEvent) return false;
    if (event.logicalKey != LogicalKeyboardKey.keyF) return false;
    if (!HardwareKeyboard.instance.isControlPressed &&
        !HardwareKeyboard.instance.isMetaPressed) {
      return false;
    }
    _openSearch(null);
    return true;
  }

  void _openSearch(String? selectedText) {
    if (!mounted) return;
    final q = selectedText?.trim();
    if (q != null && q.isNotEmpty) {
      context.go('/search?q=${Uri.encodeComponent(q)}');
      return;
    }
    context.go('/search');
  }

  @override
  Widget build(BuildContext context) {
    if (kIsWeb) return widget.child;
    return Shortcuts(
      shortcuts: const {
        SingleActivator(LogicalKeyboardKey.keyF, control: true): _OpenSearchIntent(),
        SingleActivator(LogicalKeyboardKey.keyF, meta: true): _OpenSearchIntent(),
      },
      child: Actions(
        actions: {
          _OpenSearchIntent: CallbackAction<_OpenSearchIntent>(
            onInvoke: (_) {
              _openSearch(null);
              return null;
            },
          ),
        },
        child: widget.child,
      ),
    );
  }
}
