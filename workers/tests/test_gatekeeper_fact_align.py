"""Deterministic extractors: number normalization, unit classing, and the
trace<->article numeric entailment score that backs the factuality signal."""

from app.modules.gatekeeper import fact_align as fa


def test_number_normalization_and_units() -> None:
    q = {x.raw: x for x in fa.extract_numbers("Raised $1.5M, up 12% from 50,000 users")}
    assert q["$1.5M"].value == 1_500_000.0
    assert q["$1.5M"].unit == "currency"
    assert q["12%"].value == 12.0
    assert q["12%"].unit == "percent"
    assert q["50,000"].value == 50_000.0
    assert q["50,000"].unit == "plain"


def test_word_magnitudes() -> None:
    vals = {x.value for x in fa.extract_numbers("2 billion txns and 3 thousand nodes")}
    assert 2e9 in vals and 3e3 in vals


def test_entailment_all_grounded() -> None:
    trace = '{"live_price": 0.18, "supply": 10000000000}'
    article = "ALGO trades at $0.18 against a 10 billion supply."
    r = fa.numeric_entailment_score(trace, article)
    assert r.score == 1.0
    assert r.ungrounded == ()


def test_entailment_flags_ungrounded_number() -> None:
    trace = '{"tps": 50000}'
    # 50000 is grounded; the invented 999000 is not.
    article = "It hit 50,000 TPS, with 999,000 daily users."
    r = fa.numeric_entailment_score(trace, article)
    assert r.grounded == 1
    assert "999,000" in r.ungrounded
    assert r.score == 0.5


def test_tolerance_band_accepts_rounding() -> None:
    trace = '{"holders": 50234}'
    article = "roughly 50,000 holders"  # within 2% -> grounded
    assert fa.numeric_entailment_score(trace, article, tol=0.02).score == 1.0


def test_unit_mismatch_is_not_entailed() -> None:
    trace = '{"fee": 50}'  # plain/currency
    article = "fees rose 50%"  # percent must not match a plain 50
    r = fa.numeric_entailment_score(trace, article)
    assert r.score == 0.0


def test_no_numbers_is_vacuously_grounded() -> None:
    assert fa.numeric_entailment_score("{}", "A purely qualitative update.").score == 1.0


def test_percent_suffixed_trace_value_grounds_matching_claim() -> None:
    """A genuinely server-computed percentage (e.g. get_asset_holder_share's
    share_pct, investigation_store._stringify_percent_fields applies the '%'
    suffix before storage) must actually be able to ground a matching
    article claim — before that fix, every real percentage this codebase
    computes was serialized as a bare float with no '%', so it could never
    register as a percent-class anchor at all (2026-07-14)."""
    trace = '{"asset_id": 1732165149, "share_pct": "11.2112%"}'
    article = "The creator holds about 11.21% of the token supply."
    r = fa.numeric_entailment_score(trace, article)
    assert r.score == 1.0
    assert r.ungrounded == ()


def test_fabricated_holder_percentage_not_grounded_by_real_share() -> None:
    """Regression-pin the actual CompX incident: the real, correctly-computed
    holder share (11.2112%, now percent-suffixed per the Fix 2 storage
    change) must NOT ground a fabricated, wildly different claim (99.99%) —
    confirming the gatekeeper correctly flags the fabrication as ungrounded
    once the real percentage is actually visible as a percent-class anchor,
    rather than the two both being invisible to entailment (which is what
    let the fabricated claim score gk_factuality=1.00 in the real incident)."""
    trace = (
        '{"asset_id": 1732165149, "address": "CREATOR", '
        '"holder_amount_adjusted": 112111670.453492, '
        '"total_supply_adjusted": 1000000000.0, "share_pct": "11.2112%"}'
    )
    article = "A single wallet holds 99.99% of the token supply."
    r = fa.numeric_entailment_score(trace, article)
    assert "99.99%" in r.ungrounded
    assert r.score < 0.8  # below GATEKEEPER_FACT_MIN


def test_extract_dates_formats() -> None:
    from datetime import date

    got = set(fa.extract_dates("on 18/06/2026, ISO 2026-06-01, and June 2024"))
    assert date(2026, 6, 18) in got      # DD/MM/YYYY
    assert date(2026, 6, 1) in got       # ISO
    assert date(2024, 6, 1) in got       # month + year (day defaults to 1)


def test_extract_dates_drops_noise_years() -> None:
    # A bare "2/3/1850" is below min_year and dropped.
    assert fa.extract_dates("ref 2/3/1850") == []


def test_content_recency_recent_vs_stale() -> None:
    from datetime import date

    today = date(2026, 6, 19)
    assert fa.content_recency_score("data from 18/06/2026", today=today) > 0.99
    assert fa.content_recency_score("history from 10/10/1990", today=today) == 0.0
    assert fa.content_recency_score("no dates here", today=today) is None
    # Forward-looking roadmap dates do not count as a recent event hook.
    assert fa.content_recency_score("planned for 2027-01-01", today=today) is None


def test_event_anchor_prefers_metadata_over_roadmap_text() -> None:
    from datetime import date

    anchor = fa.event_anchor_date(
        published_at="2025-11-15T10:00:00Z",
        page_title="Noah x Algorand",
        page_text="Announced November 2025. Rollout planned for 2026.",
        today=date(2026, 6, 29),
    )
    assert anchor == date(2025, 11, 15)


def test_source_timeliness_stale_pr_vs_fresh() -> None:
    from datetime import date

    today = date(2026, 6, 29)
    stale = fa.source_timeliness_score(
        published_at="2025-11-15T10:00:00Z",
        page_title="Noah partnership",
        page_text="Rollout planned for 2026.",
        today=today,
        stale_days=90,
    )
    fresh = fa.source_timeliness_score(
        published_at="2026-06-29T10:00:00Z",
        page_text="Launch this week on Algorand mainnet.",
        today=today,
        stale_days=90,
    )
    assert stale == 0.0
    assert fresh == 1.0


def test_source_timeliness_unknown_is_neutral() -> None:
    assert fa.source_timeliness_score(page_text="Algorand wallet features.") == 0.5
