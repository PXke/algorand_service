import 'dart:convert';
import 'dart:typed_data';

/// Suggested transaction parameters from algod `/v2/transactions/params`.
class SuggestedParams {
  const SuggestedParams({
    required this.feePerByte,
    required this.genesisId,
    required this.genesisHash,
    required this.lastRound,
    required this.minFee,
  });

  final int feePerByte;
  final String genesisId;
  final Uint8List? genesisHash;
  final int lastRound;
  final int minFee;

  factory SuggestedParams.fromJson(Map<String, dynamic> json) {
    final gh = json['genesis-hash'];
    Uint8List? genesisHash;
    if (gh is String && gh.isNotEmpty) {
      genesisHash = Uint8List.fromList(base64Decode(gh));
    }
    return SuggestedParams(
      feePerByte: json['fee'] as int,
      genesisId: json['genesis-id'] as String,
      genesisHash: genesisHash,
      lastRound: json['last-round'] as int,
      minFee: json['min-fee'] as int,
    );
  }
}
