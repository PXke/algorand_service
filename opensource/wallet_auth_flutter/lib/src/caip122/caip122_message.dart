import 'dart:convert';

/// CAIP-122 payload carried in ARC-0060 AUTH `data` (JSON, base64-encoded on wire).
class Caip122Message {
  const Caip122Message({
    required this.domain,
    required this.accountAddress,
    required this.uri,
    required this.chainId,
    required this.nonce,
    this.version = '1',
    this.type = 'ed25519',
    this.statement,
    this.issuedAt,
    this.expirationTime,
    this.notBefore,
    this.requestId,
    this.resources = const [],
  });

  factory Caip122Message.fromJson(Map<String, dynamic> json) {
    return Caip122Message(
      domain: json['domain'] as String,
      accountAddress: json['account_address'] as String,
      uri: json['uri'] as String,
      chainId: json['chain_id'] as String,
      nonce: json['nonce'] as String,
      version: json['version'] as String? ?? '1',
      type: json['type'] as String? ?? 'ed25519',
      statement: json['statement'] as String?,
      issuedAt: json['issued-at'] as String?,
      expirationTime: json['expiration-time'] as String?,
      notBefore: json['not-before'] as String?,
      requestId: json['request-id'] as String?,
      resources: (json['resources'] as List<dynamic>?)?.map((e) => e as String).toList() ?? const [],
    );
  }

  final String domain;
  final String accountAddress;
  final String uri;
  final String chainId;
  final String nonce;
  final String version;
  final String type;
  final String? statement;
  final String? issuedAt;
  final String? expirationTime;
  final String? notBefore;
  final String? requestId;
  final List<String> resources;

  Map<String, dynamic> toJson() {
    final map = <String, dynamic>{
      'domain': domain,
      'account_address': accountAddress,
      'uri': uri,
      'version': version,
      'chain_id': chainId,
      'nonce': nonce,
      'type': type,
    };
    if (statement != null) map['statement'] = statement;
    if (issuedAt != null) map['issued-at'] = issuedAt;
    if (expirationTime != null) map['expiration-time'] = expirationTime;
    if (notBefore != null) map['not-before'] = notBefore;
    if (requestId != null) map['request-id'] = requestId;
    if (resources.isNotEmpty) map['resources'] = resources;
    return map;
  }

  String toCanonicalJson() => canonicalJsonEncode(toJson());

  String toDataBase64() => base64Encode(utf8.encode(toCanonicalJson()));
}

String canonicalJsonEncode(Map<String, dynamic> object) {
  return jsonEncode(_sortValue(object));
}

dynamic _sortValue(dynamic value) {
  if (value is Map) {
    final keys = value.keys.map((k) => k.toString()).toList()..sort();
    return {for (final key in keys) key: _sortValue(value[key])};
  }
  if (value is List) {
    return value.map(_sortValue).toList();
  }
  return value;
}
