USE algorand_platform;

-- Slug uniqueness/lookup moves fully onto `articles` itself, following the
-- exact precedent 067_articles_consolidated.cql already set for article_id:
-- SAI supports point queries that don't include the partition key, so
-- `SELECT article_id FROM articles WHERE slug = ?` works directly, in one
-- round-trip, with no second table to keep in sync -- closing the class of
-- bug a claim recorded in articles_by_slug that never got written back onto
-- the owning article's own slug column (found 2026-08-27, following the
-- night's earlier duplicate-article incidents).
--
-- articles_by_slug's one real advantage over this -- an atomic IF NOT EXISTS
-- claim, guaranteeing two concurrent writers can never both win the same
-- slug -- is traded away deliberately: this pipeline composes and publishes
-- one article at a time (single compose slot, 1-slot review queue,
-- per-service cooldown), so two writers racing on the literal same slug
-- text is not a real exposure in practice. ensure_article_slug
-- (shared/algorand_shared/slugs.py) now does a single check-then-write
-- directly against `articles`, closing the write-back-drift class by
-- construction instead of needing a reconciliation beat to detect it.
CREATE CUSTOM INDEX IF NOT EXISTS articles_slug_sai
  ON articles (slug) USING 'sai';

DROP TABLE IF EXISTS articles_by_slug;
