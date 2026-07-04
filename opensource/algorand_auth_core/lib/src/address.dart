import 'dart:typed_data';

import 'package:base32/base32.dart';
import 'package:collection/collection.dart';
import 'package:crypto/crypto.dart';

/// Decoded Algorand account address (32-byte Ed25519 public key).
class AlgorandAddress {
  const AlgorandAddress(this.publicKey);

  static const publicKeyLength = 32;
  static const checksumLength = 4;

  final Uint8List publicKey;

  /// Decode a base32-checksummed Algorand address.
  factory AlgorandAddress.decode(String address) {
    final addressBytes = base32.decode(address);
    if (addressBytes.length != publicKeyLength + checksumLength) {
      throw FormatException('Invalid Algorand address length');
    }
    final key = Uint8List.fromList(addressBytes.sublist(0, publicKeyLength));
    final checksum =
        addressBytes.sublist(publicKeyLength, publicKeyLength + checksumLength);
    final expected = sha512256
        .convert(key)
        .bytes
        .sublist(publicKeyLength - checksumLength);
    if (!const ListEquality<int>().equals(expected, checksum)) {
      throw FormatException('Invalid Algorand address checksum');
    }
    return AlgorandAddress(key);
  }
}
