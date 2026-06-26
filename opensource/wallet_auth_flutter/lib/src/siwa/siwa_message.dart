import '../caip122/caip122_message.dart';

/// SIWA / EIP-4361 human-readable sign-in message (@avmkit/siwa compatible).
class SiwaMessage {
  const SiwaMessage({
    required this.domain,
    required this.accountAddress,
    required this.uri,
    required this.chainId,
    required this.nonce,
    this.statement,
    this.version = '1',
    this.issuedAt,
    this.expirationTime,
    this.notBefore,
    this.requestId,
    this.resources = const [],
    this.scheme,
  });

  factory SiwaMessage.fromCaip122(Caip122Message caip122, {required int walletConnectChainId}) {
    return SiwaMessage(
      domain: caip122.domain,
      accountAddress: caip122.accountAddress,
      uri: caip122.uri,
      chainId: walletConnectChainId,
      nonce: caip122.nonce,
      statement: caip122.statement,
      version: caip122.version,
      issuedAt: caip122.issuedAt,
      expirationTime: caip122.expirationTime,
      notBefore: caip122.notBefore,
      requestId: caip122.requestId,
      resources: caip122.resources,
    );
  }

  final String domain;
  final String accountAddress;
  final String uri;
  final int chainId;
  final String nonce;
  final String? statement;
  final String version;
  final String? issuedAt;
  final String? expirationTime;
  final String? notBefore;
  final String? requestId;
  final List<String> resources;
  final String? scheme;

  String prepareMessage() {
    if (statement != null && statement!.contains('\n')) {
      throw ArgumentError('statement must not contain newlines');
    }

    final headerPrefix = scheme != null ? '$scheme://$domain' : domain;
    final header = '$headerPrefix wants you to sign in with your Algorand account:';
    var prefix = '$header\n$accountAddress';

    if (statement != null && statement!.isNotEmpty) {
      prefix = '$prefix\n\n$statement\n';
    }

    final suffixParts = <String>[
      'URI: $uri',
      'Version: $version',
      'Chain ID: $chainId',
      'Nonce: $nonce',
    ];

    if (issuedAt != null) suffixParts.add('Issued At: $issuedAt');
    if (expirationTime != null) suffixParts.add('Expiration Time: $expirationTime');
    if (notBefore != null) suffixParts.add('Not Before: $notBefore');
    if (requestId != null) suffixParts.add('Request ID: $requestId');
    if (resources.isNotEmpty) {
      suffixParts.add('Resources:\n${resources.map((r) => '- $r').join('\n')}');
    }

    return '$prefix\n${suffixParts.join('\n')}';
  }
}
