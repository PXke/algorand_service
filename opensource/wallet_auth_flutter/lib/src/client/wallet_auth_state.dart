/// Immutable auth state exposed by [WalletAuthClient].
class WalletAuthState {
  const WalletAuthState({
    this.walletAddress,
    this.sessionToken,
    this.isLoading = false,
    this.error,
  });

  final String? walletAddress;
  final String? sessionToken;
  final bool isLoading;
  final Object? error;

  bool get isAuthenticated =>
      walletAddress != null && walletAddress!.isNotEmpty && sessionToken != null;

  WalletAuthState copyWith({
    String? walletAddress,
    String? sessionToken,
    bool? isLoading,
    Object? error,
    bool clearError = false,
    bool clearSession = false,
  }) {
    return WalletAuthState(
      walletAddress: clearSession ? null : (walletAddress ?? this.walletAddress),
      sessionToken: clearSession ? null : (sessionToken ?? this.sessionToken),
      isLoading: isLoading ?? this.isLoading,
      error: clearError ? null : (error ?? this.error),
    );
  }
}
