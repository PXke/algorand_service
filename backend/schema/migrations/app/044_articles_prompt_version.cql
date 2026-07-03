USE algorand_platform;

-- Compose prompts (mistral_compose.py's system/user templates) change over time
-- with no way to tell which prompt produced a given stored article, so a bad
-- rule edit or a genuine improvement is invisible to analytics. Stamp each
-- article with the PROMPT_VERSION constant that was live when it was written
-- (empty/null for template-composed articles, which have no prompt) so grade
-- and engagement trends can be correlated with a specific prompt change.
ALTER TABLE articles_by_id ADD prompt_version text;
