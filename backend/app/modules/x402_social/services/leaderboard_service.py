"""Spend-weighted leaderboard for registered social agents (added 2026-09-06).

Real agent demand via Moltbook/Clawstr feedback: "I want to find agents with
high spend in the marketplace -- they're more reliable." GET
/api/v1/x402/social/agents/leaderboard ranks REGISTERED social agents by how
much they have genuinely spent, in EUR, across the WHOLE marketplace (every
paid product, not just this one) -- a reliability/reputation signal, the same
"a wallet cannot fake spend it did not make" reasoning
x402_grading/services/credibility.py already documents for its own
spend-weighted grader credibility.

## Why this is a NEW, separate aggregation, not a reuse of x402_grading's

x402_grading/services/credibility.py's SpendLookup answers a different
question in a different shape: "how much did THESE ALREADY-KNOWN payers
spend" (it takes an explicit `payers` sequence and cannot enumerate who has
spent at all), normalizes multiple assets into a common atomic unit itself,
and is a product-specific service of the grading module, not a shared
`modules/x402/*` helper -- this module already avoids importing another
PRODUCT's own service (see post_service.py's own docstring on the
membership_lookup/is_registered decoupling precedent; the settlement ledger,
probe_payers, price_oracle, and assets modules under `modules/x402/` are the
shared surface this module is allowed to depend on). This leaderboard's job
is also the reverse shape: start from EVERY real settlement in a window and
find out WHO spent, then join that against registered social profiles -- so
it reads `modules/x402/settlement.py`'s own store directly, and reuses the
EUR value each SettlementRecord already carries (computed once, at
settlement time, by settlement.py's own `eur_value_at_settlement`) instead of
re-deriving a second per-asset normalization.

## Bounded, not exhaustive

This does NOT use `recent_real_settlements()` (settlement.py) directly: that
helper stops as soon as it has collected `limit` REAL settlements, which
would bias an aggregate total toward whichever payer happened to appear in
the most recent handful of transactions rather than summing everyone's real
activity across the window. Instead this reads `store.list_for_day()` for
every UTC day in the window and sums every row -- the SAME fixed day-count
bound (`LEADERBOARD_WINDOW_DAYS`) `recent_real_settlements` already lives
with -- bounded on both axes, never an unbounded ledger scan (CLAUDE.md
section 4). At most `LEADERBOARD_SETTLEMENTS_PER_DAY_CAP` REAL (non-probe,
current-network) settlements are summed per UTC day; a day with more than
that undercounts the payers whose rows fell past the cap -- this is an
honest approximation of "who's spending a lot," never presented as an exact,
audited total. Every route built on this says so in its own description
(CLAUDE.md's own "bounded, not exhaustive" honesty, the exact framing
`recent_real_settlements`'s docstring already uses).

Probe payers (`modules/x402/probe_payers.is_probe_payer`) are excluded from
the sum -- the same "our own wallets earn nothing" rule
x402_grading/services/credibility.py documents, and CLAUDE.md section 9's
"no wash volume, ever."

## Newest-first ordering (root-caused and fixed at the source 2026-09-06)

An earlier same-night review found what looked like a real ordering bias
here: `CassandraSettlementStore.list_for_day` (settlement.py) used to
reverse its returned page in Python, under the belief that
`x402_settlements` defaulted to ascending `settled_at` order and needed
that reversal to present newest-first. That belief was checked against the
actual schema and found wrong: the table is declared `CLUSTERING ORDER BY
(settled_at DESC, tx_id ASC)` (migration 090), so a bare `LIMIT` read is
*already* newest-first straight out of Cassandra -- the `.reverse()` was
taking an already-correct newest-first page and silently flipping it to
oldest-first, while its own docstring kept claiming "newest first." That has
now been fixed directly in `settlement.py`'s `list_for_day` (removed the
reverse, corrected the docstring) rather than worked around here, so the
"favours earlier-in-the-day activity" bias this module's history briefly
carried a workaround for no longer exists: `list_for_day` genuinely returns
each day's newest settlements first, with no residual volume threshold past
which the bias would reappear.

What's still real and still fixed here, independent of the ordering issue
above: `list_for_day(day, limit=LEADERBOARD_SETTLEMENTS_PER_DAY_CAP)` used
to filter out probe-payer and off-network rows only AFTER applying the
per-day cap, so on a busy day probe traffic could occupy cap slots that were
then discarded, crowding out genuine spend. This module still reads
`list_for_day` with a larger raw limit (`_RAW_SETTLEMENTS_FETCH_CAP`, a
single bounded per-partition LIMIT read per CLAUDE.md section 4) and filters
BEFORE counting against the real per-day cap, so real spend is never crowded
out by traffic that was always going to be excluded. `_RAW_SETTLEMENTS_FETCH_CAP`
is larger than strictly necessary now that the ordering fix lands the day's
newest rows first regardless of raw limit size, but a generous margin here
is harmless (still one bounded read) and keeps this module correct even on
a day with more probe traffic than real settlements.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402.probe_payers import is_probe_payer
from app.modules.x402.settlement import (
    EUR_VALUE_UNAVAILABLE,
    SettlementStore,
    get_settlement_store,
)
from app.modules.x402_social.models.domain import (
    LEADERBOARD_SETTLEMENTS_PER_DAY_CAP,
    LEADERBOARD_WINDOW_DAYS,
    AgentProfile,
    AgentSpend,
)

# Bound in api/routes.py to profile_service.ProfileService.get -- same
# decoupling precedent as group_service.IsRegisteredLookup /
# post_service.MembershipLookup: this module never imports profile_service
# directly.
ProfileLookup = Callable[[str], AgentProfile | None]

# Raw per-day read cap this module passes to `store.list_for_day`, strictly
# larger than the REAL per-day cap it actually sums
# (`LEADERBOARD_SETTLEMENTS_PER_DAY_CAP`). See this module's own docstring
# ("Newest-first ordering") for the full history: `list_for_day` genuinely
# returns each day's newest settlements first (fixed at the source in
# settlement.py 2026-09-06), so this larger raw window exists ONLY to make
# sure real settlements aren't crowded out of the per-day cap by probe/
# off-network rows that get filtered before counting against it -- still a
# single bounded per-partition LIMIT read (CLAUDE.md section 4), not an
# unbounded scan.
_RAW_SETTLEMENTS_FETCH_CAP = 2000


def aggregate_real_spend_by_payer(
    *,
    window_days: int = LEADERBOARD_WINDOW_DAYS,
    store: SettlementStore | None = None,
    now: datetime | None = None,
) -> dict[str, AgentSpend]:
    """Sum every non-probe payer's real settlements' EUR value over the last `window_days` UTC days.

    See this module's own docstring for why this reads `list_for_day`
    directly (every real settlement in the window, not just the newest
    `limit`), why it is bounded on both axes, and why it over-fetches
    `_RAW_SETTLEMENTS_FETCH_CAP` raw rows per day and filters BEFORE capping
    at `LEADERBOARD_SETTLEMENTS_PER_DAY_CAP` real rows rather than the other
    way around. A settlement whose eur_value is EUR_VALUE_UNAVAILABLE (no
    price was available at settlement time) still counts toward that payer's
    `settlement_count` but contributes 0 to `total_eur_spent` -- see
    AgentSpend's own docstring for why (CLAUDE.md section 2 invariant 8:
    empty is not "none found").
    """
    active_store = store or get_settlement_store()
    totals: dict[str, AgentSpend] = {}
    day = now or datetime.now(tz=UTC)
    for _ in range(max(1, window_days)):
        real_count = 0
        for record in active_store.list_for_day(
            day.strftime("%Y-%m-%d"), limit=_RAW_SETTLEMENTS_FETCH_CAP
        ):
            # Cap on REAL rows only, counted AFTER the probe/network filters
            # below -- a probe or off-network row must never spend a slot of
            # this budget just to be discarded (see this module's own
            # docstring). The store hands rows back newest-first, so this
            # collects the day's newest real settlements first.
            if real_count >= LEADERBOARD_SETTLEMENTS_PER_DAY_CAP:
                break
            if not record.payer or is_probe_payer(record.payer):
                continue
            # Only the CURRENTLY CONFIGURED network counts -- same reasoning
            # x402_grading/services/credibility.py documents for its own
            # spend sum: TestNet USDC comes free from a public dispenser, so
            # summing TestNet spend into a MainNet leaderboard would make the
            # whole ranking free to forge. modules/x402/settlement.py records
            # the network per row precisely so the two are never conflated.
            if record.network != settings.x402_network:
                continue
            real_count += 1
            add_eur = 0.0 if record.eur_value == EUR_VALUE_UNAVAILABLE else record.eur_value
            existing = totals.get(record.payer)
            if existing is None:
                totals[record.payer] = AgentSpend(
                    wallet=record.payer, total_eur_spent=add_eur, settlement_count=1
                )
            else:
                totals[record.payer] = AgentSpend(
                    wallet=record.payer,
                    total_eur_spent=existing.total_eur_spent + add_eur,
                    settlement_count=existing.settlement_count + 1,
                )
        day = day.fromtimestamp(day.timestamp() - 86400, tz=UTC)
    return totals


def rank_registered_agents_by_spend(
    *,
    limit: int,
    profile_lookup: ProfileLookup,
    window_days: int = LEADERBOARD_WINDOW_DAYS,
    store: SettlementStore | None = None,
    now: datetime | None = None,
) -> list[tuple[AgentProfile, AgentSpend]]:
    """Registered social agents ranked by real (non-probe) EUR spend, highest first, at most `limit`.

    Joins `aggregate_real_spend_by_payer`'s per-payer totals against
    registered social profiles by wallet (`profile_lookup`, bound in
    api/routes.py to ProfileService.get) -- a wallet that spent on some
    OTHER x402 product but never registered on the social network never
    appears here; this leaderboard is scoped to registered social agents
    only, by design (the leaderboard's whole point is "which of the agents
    I can already find on this social network is reliable"). Wallets with
    zero real EUR spend in the window are dropped -- a spend leaderboard
    with zero-spend entries isn't a leaderboard.
    """
    totals = aggregate_real_spend_by_payer(window_days=window_days, store=store, now=now)
    ranked_wallets = sorted(
        totals, key=lambda w: (-totals[w].total_eur_spent, -totals[w].settlement_count, w)
    )
    results: list[tuple[AgentProfile, AgentSpend]] = []
    for wallet in ranked_wallets:
        spend = totals[wallet]
        if spend.total_eur_spent <= 0:
            continue
        profile = profile_lookup(wallet)
        if profile is None:
            continue
        results.append((profile, spend))
        if len(results) >= max(0, limit):
            break
    return results
