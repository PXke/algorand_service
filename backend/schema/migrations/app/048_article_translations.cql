USE algorand_platform;

ALTER TABLE articles_feed ADD translations map<text, text>;
ALTER TABLE articles_by_id ADD translations map<text, text>;
