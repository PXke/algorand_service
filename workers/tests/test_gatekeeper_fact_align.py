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
    # Future/scheduled date clamps to fully recent.
    assert fa.content_recency_score("event 2027-01-01", today=today) == 1.0
