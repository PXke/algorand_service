USE algorand_platform;

ALTER TABLE articles_by_id ADD tags list<text>;
ALTER TABLE articles_feed ADD tags list<text>;
