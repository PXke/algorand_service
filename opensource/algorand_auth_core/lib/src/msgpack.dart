import 'dart:collection';
import 'dart:convert';
import 'dart:typed_data';

import 'package:msgpack_dart/msgpack_dart.dart';

/// Algorand msgpack encoding (sorted keys, omit zero/empty fields).
class AlgorandMsgpack {
  static Uint8List encode(Map<String, dynamic> data) {
    return Uint8List.fromList(serialize(_sanitize(_prepare(data))));
  }

  static Map<String, dynamic> _prepare(Map<String, dynamic> data) {
    final out = <String, dynamic>{};
    data.forEach((key, value) {
      var v = value;
      if (value is Map<String, dynamic>) {
        v = _prepare(value);
      } else if (value is List<Map<String, dynamic>>) {
        v = value.map(_prepare).toList();
      }
      out[key] = v;
    });
    return out;
  }

  static Map<String, dynamic> _sanitize(Map<String, dynamic> data) {
    final sorted = SplayTreeMap<String, dynamic>.from(data);
    sorted.removeWhere(
      (key, value) =>
          value == null ||
          value == false ||
          (value is Map && value.isEmpty) ||
          (value is List && value.isEmpty) ||
          (value is String && value.isEmpty) ||
          (value is int && value == 0),
    );
    return sorted;
  }
}
