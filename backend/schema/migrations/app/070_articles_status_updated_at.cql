USE algorand_platform;

-- Generic "when did status last change" timestamp -- closes a real gap found
-- while migrating list_draft_articles() (backend admin) off the legacy
-- draft_articles table: that table's own drafted_at column had no
-- equivalent on `articles` at all. deleted_at already covers the 'deleted'
-- transition specifically -- status_updated_at covers every OTHER
-- transition (draft, backlog, on_hold, published) with one column instead
-- of one per status. Nullable -- pre-existing rows from before this column
-- existed have no value until their next transition.
ALTER TABLE articles ADD status_updated_at timestamp;
