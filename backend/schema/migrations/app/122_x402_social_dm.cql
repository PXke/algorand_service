USE algorand_platform;

-- x402 agent social network: private messages (DMs) between two registered
-- agents (operator ask 2026-09-07: "Did we do the DM in our social
-- network?" -- no, until this migration). Free, session-authenticated (the
-- POST /auth/session bearer token this module already mints identifies the
-- sender) rather than a PAID payer-is-identity write like posts/comments/
-- reactions/follows -- see app/modules/x402_social/api/routes.py's
-- x402_social_dm_send docstring for why: a DM is never publicly visible, so
-- unlike a post or a follow there is no marketplace-visible product to
-- price, and the abuse control this feature actually needs (CLAUDE.md
-- section 9: rate limit every free endpoint per wallet AND per IP) can only
-- be enforced BEFORE a message is accepted if the sender's identity is
-- already known pre-action -- a paid route's payer-is-identity convention
-- (design doc section 4.1) cannot give that, since the payer is only known
-- AFTER settlement. See services/dm_service.py and services/rate_limit.py's
-- dm_send_wallet_rate_limited / dm_send_ip_rate_limited.

-- One conversation between exactly two wallets, identified by
-- dm_service.conversation_id_for(wallet_a, wallet_b) -- the hex SHA-256 of
-- the two wallets sorted then joined, the same "hash the thing that must be
-- unique" precedent x402_social_groups' own name-derived group_id already
-- uses (see group_service.group_id_for). Canonical, time-ordered message
-- log for ONE conversation -- the query pattern GET /dm/{wallet} (a
-- caller's own conversation with one peer, newest-first, LIMIT-bounded)
-- actually needs -- both participants read the SAME partition.
CREATE TABLE IF NOT EXISTS x402_social_dm_messages (
  conversation_id text,
  created_at timestamp,
  message_id timeuuid,
  sender text,
  recipient text,
  body text,
  PRIMARY KEY ((conversation_id), created_at, message_id)
) WITH CLUSTERING ORDER BY (created_at DESC, message_id DESC);

-- One row per (wallet, peer_wallet) -- a wallet's own conversation list.
-- Upserted in place on BOTH sides on every new message (idempotent
-- overwrite, the same "re-stamp on write, never a delete-then-reinsert"
-- shape x402_social_follows already uses for "most recently followed").
-- NOT clustered by recency, same reasoning as x402_social_follows /
-- x402_social_memberships (106's own comment): a wallet's conversation
-- count is expected to stay small at competition scale, so "most recently
-- active first" (GET /dm) is a bounded single-partition scan (LIMIT
-- domain.DM_CONVERSATION_SCAN_LIMIT) sorted by last_message_at in Python --
-- see graph_service's identical following()/followers() bounded-scan-then-
-- sort trade for the precedent this mirrors.
CREATE TABLE IF NOT EXISTS x402_social_dm_conversations (
  wallet text,
  peer_wallet text,
  conversation_id text,
  last_message_at timestamp,
  last_sender text,
  last_message_preview text,
  PRIMARY KEY ((wallet), peer_wallet)
);
