"""Deterministic extractors: number normalization, unit classing, and the trace<->article numeric entailment score that backs the factuality signal."""

from app.modules.gatekeeper import fact_align as fa


def test_number_normalization_and_units() -> None:
    """Normalizes currency, percent, and plain numbers to values and unit classes."""
    q = {x.raw: x for x in fa.extract_numbers("Raised $1.5M, up 12% from 50,000 users")}
    assert q["$1.5M"].value == 1_500_000.0
    assert q["$1.5M"].unit == "currency"
    assert q["12%"].value == 12.0
    assert q["12%"].unit == "percent"
    assert q["50,000"].value == 50_000.0
    assert q["50,000"].unit == "plain"


def test_word_magnitudes() -> None:
    """Expands word-magnitude numbers like "2 billion" and "3 thousand" to their numeric value."""
    vals = {x.value for x in fa.extract_numbers("2 billion txns and 3 thousand nodes")}
    assert 2e9 in vals
    assert 3e3 in vals


def test_entailment_all_grounded() -> None:
    """Scores 1.0 when every article number is present in the trace."""
    trace = '{"live_price": 0.18, "supply": 10000000000}'
    article = "ALGO trades at $0.18 against a 10 billion supply."
    r = fa.numeric_entailment_score(trace, article)
    assert r.score == 1.0
    assert r.ungrounded == ()


def test_entailment_flags_ungrounded_number() -> None:
    """Flags an article number absent from the trace and halves the score."""
    trace = '{"tps": 50000}'
    # 50000 is grounded; the invented 999000 is not.
    article = "It hit 50,000 TPS, with 999,000 daily users."
    r = fa.numeric_entailment_score(trace, article)
    assert r.grounded == 1
    assert "999,000" in r.ungrounded
    assert r.score == 0.5


def test_tolerance_band_accepts_rounding() -> None:
    """Grounds a rounded article number within the configured tolerance band."""
    trace = '{"holders": 50234}'
    article = "roughly 50,000 holders"  # within 2% -> grounded
    assert fa.numeric_entailment_score(trace, article, tol=0.02).score == 1.0


def test_unit_mismatch_is_not_entailed() -> None:
    """Does not ground a percent claim by a same-valued plain-number trace entry."""
    trace = '{"fee": 50}'  # plain/currency
    article = "fees rose 50%"  # percent must not match a plain 50
    r = fa.numeric_entailment_score(trace, article)
    assert r.score == 0.0


def test_no_numbers_is_vacuously_grounded() -> None:
    """Scores 1.0 for article text that contains no numeric claims at all."""
    assert fa.numeric_entailment_score("{}", "A purely qualitative update.").score == 1.0


def test_percent_suffixed_trace_value_grounds_matching_claim() -> None:
    """A genuinely server-computed percentage (e.g. get_asset_holder_share's share_pct, investigation_store._stringify_percent_fields applies the '%' suffix before storage) must actually be able to ground a matching article claim — before that fix, every real percentage this codebase computes was serialized as a bare float with no '%', so it could never register as a percent-class anchor at all (2026-07-14)."""
    trace = '{"asset_id": 1732165149, "share_pct": "11.2112%"}'
    article = "The creator holds about 11.21% of the token supply."
    r = fa.numeric_entailment_score(trace, article)
    assert r.score == 1.0
    assert r.ungrounded == ()


def test_fabricated_holder_percentage_not_grounded_by_real_share() -> None:
    """Regression-pin the actual CompX incident: the real, correctly-computed holder share (11.2112%, now percent-suffixed per the Fix 2 storage change) must NOT ground a fabricated, wildly different claim (99.99%) — confirming the gatekeeper correctly flags the fabrication as ungrounded once the real percentage is actually visible as a percent-class anchor, rather than the two both being invisible to entailment (which is what let the fabricated claim score gk_factuality=1.00 in the real incident)."""
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
    """Parses DD/MM/YYYY, ISO, and "Month YYYY" date formats out of text."""
    from datetime import date

    got = set(fa.extract_dates("on 18/06/2026, ISO 2026-06-01, and June 2024"))
    assert date(2026, 6, 18) in got  # DD/MM/YYYY
    assert date(2026, 6, 1) in got  # ISO
    assert date(2024, 6, 1) in got  # month + year (day defaults to 1)


def test_extract_dates_drops_noise_years() -> None:
    """Drops a date below the minimum plausible year instead of extracting it."""
    # A bare "2/3/1850" is below min_year and dropped.
    assert fa.extract_dates("ref 2/3/1850") == []


def test_content_recency_recent_vs_stale() -> None:
    """Scores recent dates near 1.0, old dates 0.0, and dateless/forward-looking text as None."""
    from datetime import date

    today = date(2026, 6, 19)
    assert fa.content_recency_score("data from 18/06/2026", today=today) > 0.99
    assert fa.content_recency_score("history from 10/10/1990", today=today) == 0.0
    assert fa.content_recency_score("no dates here", today=today) is None
    # Forward-looking roadmap dates do not count as a recent event hook.
    assert fa.content_recency_score("planned for 2027-01-01", today=today) is None


def test_event_anchor_prefers_metadata_over_roadmap_text() -> None:
    """Anchors the event date to published_at metadata rather than a forward-looking roadmap date in the body."""
    from datetime import date

    anchor = fa.event_anchor_date(
        published_at="2025-11-15T10:00:00Z",
        page_title="Noah x Algorand",
        page_text="Announced November 2025. Rollout planned for 2026.",
        today=date(2026, 6, 29),
    )
    assert anchor == date(2025, 11, 15)


def test_source_timeliness_stale_pr_vs_fresh() -> None:
    """Scores a stale-published, roadmap-only source 0.0 and a freshly published one 1.0."""
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
    """Scores 0.5 (neutral) when no published date or event date can be determined."""
    assert fa.source_timeliness_score(page_text="Algorand wallet features.") == 0.5


# --- unit-class widening (2026-07-18, quantum-rebrand incident) -------------
# The draft asserted "1,024-bit keys for Falcon-1024" and a fabricated
# "10-100x slower to verify" benchmark; the trace held the Foundation's real
# size table ("Falcon-1024 1793 ~1280", Ed25519 32/64). Percent-class
# isolation existed; bits/bytes/multiplier did not, so plain-number
# collisions ("Falcon-1024" the identifier) could ground the invented
# "1,024-bit" claim.

QUANTUM_TRACE = (
    "The key sizes of both schemes are several times larger than Ed25519: "
    "Scheme Public key size (bytes) Signature size (bytes) "
    "Ed25519 32 64 Falcon-512 897 ~640 Falcon-1024 1793 ~1280"
)


def test_identifier_number_never_grounds_bit_claim() -> None:
    """Falcon-1024 is an identifier, not a key size — it must not ground the fabricated "1,024-bit keys" claim."""
    r = fa.numeric_entailment_score(QUANTUM_TRACE, "Falcon uses 1,024-bit keys.")
    assert r.grounded == 0
    assert any("bit" in u for u in r.ungrounded)


def test_fabricated_multiplier_is_ungrounded_even_with_bare_matches() -> None:
    """Does not let a bare "100" in the trace ground a fabricated "100x" multiplier claim."""
    trace = QUANTUM_TRACE + " Throughput factor 100 was mentioned somewhere."
    r = fa.numeric_entailment_score(trace, "Verification is 100x slower.")
    assert any(u.endswith("x") for u in r.ungrounded)


def test_true_byte_claim_grounded_by_bare_table_numbers() -> None:
    """Trace tool output carries byte sizes as bare table numbers — a true "1,793-byte" claim is genuinely grounded by them (bytes stays plain-compatible, unlike bits/multiplier)."""
    r = fa.numeric_entailment_score(QUANTUM_TRACE, "Falcon-1024 public keys are 1,793-byte.")
    # claims: 1024 (identifier, grounded by trace's own Falcon-1024) and
    # 1,793-byte (grounded by the bare 1793 in the table)
    assert r.ungrounded == ()


def test_byte_trace_grounds_byte_claim_same_class() -> None:
    """Grounds a byte-unit article claim by a matching byte-unit trace value."""
    r = fa.numeric_entailment_score("signature is 640 bytes", "The signature weighs 640 bytes.")
    assert r.ungrounded == ()


def test_percent_isolation_unchanged_by_widening() -> None:
    """Keeps percent-class isolation intact after the bits/bytes/multiplier unit widening."""
    r = fa.numeric_entailment_score("value 50 appears", "growth was 50%")
    assert r.grounded == 0
