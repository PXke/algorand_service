USE algorand_platform;

-- url_queue.py reads `status` on the by-url dedupe lookup table but
-- migration 016 shipped the table without that column
ALTER TABLE url_queue_by_url ADD status text;
