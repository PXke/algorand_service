/// Persists backend session tokens (inject your own for tests or custom backends).
abstract class SessionStorage {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}
