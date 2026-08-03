-- Special edition flag on editorial briefs: an admin-picked topic that gets
-- a deeper, longer compose pass and a reader-facing badge instead of the
-- standard "scale length to substance" treatment every other brief gets.

ALTER TABLE editorial_briefs ADD is_special_edition boolean;
