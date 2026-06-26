USE algorand_platform;

-- Distinguish manually-seeded crawl starting points (origin=seed) from domains
-- promoted by the frontier on approval (origin=domain). The Seeds admin tab
-- shows only seeds and approved domains live in the Domains tab.
ALTER TABLE service_registry ADD origin text;
