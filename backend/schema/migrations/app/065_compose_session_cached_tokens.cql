-- DeepSeek (and other OpenAI-compatible providers) report prompt-cache hit
-- tokens on every response -- confirmed live 2026-08-17, previously parsed
-- but discarded by _record_usage. Adding a column so cache-hit rate is
-- visible in our own compose_sessions data instead of only on the
-- provider's own billing dashboard.
ALTER TABLE compose_sessions ADD cached_tokens int;
