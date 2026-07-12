import 'dart:js_interop';

import 'package:web/web.dart' as web;

import 'find_shortcut_stub.dart';

JSFunction? _listener;
FindShortcutHandler? _handler;

void installWebFindShortcut(FindShortcutHandler handler) {
  uninstallWebFindShortcut();
  _handler = handler;
  _listener = ((web.Event event) {
    final e = event as web.KeyboardEvent;
    if (!(e.ctrlKey || e.metaKey) || e.key != 'f') return;
    e.preventDefault();
    e.stopPropagation();
    final sel = web.window.getSelection();
    final raw = sel?.toString().trim() ?? '';
    _handler?.call(raw.isNotEmpty ? raw : null);
  }).toJS;
  web.document.addEventListener('keydown', _listener!);
}

void uninstallWebFindShortcut() {
  if (_listener != null) {
    web.document.removeEventListener('keydown', _listener!);
    _listener = null;
  }
  _handler = null;
}
