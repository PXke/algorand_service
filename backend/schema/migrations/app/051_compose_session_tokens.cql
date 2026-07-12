USE algorand_platform;

-- Cumulative Mistral token usage for one compose session, summed across every
-- chat_completion/chat_json_object/chat_with_tools round on both the research
-- and writer/reviser clients (see MistralClient.usage_totals in
-- app/modules/ai/mistral_client.py). NULL for sessions recorded before this
-- migration -- the admin UI treats a missing value as unknown, not zero.
ALTER TABLE compose_sessions ADD prompt_tokens int;
ALTER TABLE compose_sessions ADD completion_tokens int;
ALTER TABLE compose_sessions ADD total_tokens int;
