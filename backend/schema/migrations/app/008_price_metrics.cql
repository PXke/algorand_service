USE algorand_platform;

-- Time-series spot samples (CoinGecko polls).
CREATE TABLE IF NOT EXISTS price_metric_samples (
  asset_id text,
  collected_at timestamp,
  price_usd double,
  currency text,
  change_24h_pct double,
  market_cap_usd double,
  volume_24h_usd double,
  source text,
  PRIMARY KEY ((asset_id), collected_at)
) WITH CLUSTERING ORDER BY (collected_at DESC);

-- Latest prepared narrative block for Mistral article generation.
CREATE TABLE IF NOT EXISTS price_metrics_brief (
  asset_id text PRIMARY KEY,
  prepared_at timestamp,
  asset_name text,
  currency text,
  current_price_usd double,
  change_24h_pct double,
  sample_count_24h int,
  sample_count_7d int,
  mistral_context text
);
