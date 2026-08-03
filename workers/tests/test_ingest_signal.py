"""Date-only content changes hash the same (must not trigger a re-publish)."""

from app.modules.newspaper.ingest_signal import _stable_content_hash, _strip_repeating_row_blocks

_NFD_SALES_TABLE = """Recent sales
The latest primary and secondary NFD sales
View all
Name Price Seller Buyer Transaction
aigirlfriend.algo A 144.34
nfdomains.algo
gf.algo
Jul 24, 2026 12:13 AM
nunyafuckinbizness.algo A 72.42
nfdomains.algo
nunyafuckinbizness.algo
Jul 23, 2026 6:41 PM
agent402.algo A 71.1
nfdomains.algo
agent402.algo
Jul 23, 2026 3:45 AM
"""

_NFD_SALES_TABLE_DIFFERENT_ROWS = """Recent sales
The latest primary and secondary NFD sales
View all
Name Price Seller Buyer Transaction
twin.algo A 1215.6
nfdomains.algo
52NNOZ…PC6A
Aug 2, 2026 4:40 AM
plsg.algo A 151.57
nfdomains.algo
PXQ4SI…7N6Y
Jul 31, 2026 6:30 AM
espn2.algo A 75.78
nfdomains.algo
PXQ4SI…7N6Y
Jul 31, 2026 6:29 AM
"""


def test_date_only_change_hashes_the_same() -> None:
    """Two pages differing only in a long-form date produce the same stable content hash."""
    before = "ZK ColorSort daily puzzle. Last updated: June 18, 2026."
    after = "ZK ColorSort daily puzzle. Last updated: July 6, 2026."
    assert _stable_content_hash(before) == _stable_content_hash(after)


def test_iso_date_only_change_hashes_the_same() -> None:
    """Two pages differing only in an ISO-format date produce the same stable content hash."""
    before = "Snapshot generated 2026-06-18."
    after = "Snapshot generated 2026-07-06."
    assert _stable_content_hash(before) == _stable_content_hash(after)


def test_real_text_change_still_hashes_differently() -> None:
    """A genuine content change still produces a different hash even when the date stays the same."""
    before = "ZK ColorSort daily puzzle. Last updated: June 18, 2026."
    after = "ZK ColorSort now supports multiplayer mode. Last updated: June 18, 2026."
    assert _stable_content_hash(before) != _stable_content_hash(after)


def test_live_activity_table_churn_hashes_the_same() -> None:
    """NFDomains regression pin (2026-08-02): a marketplace 'recent sales' table flattened to a 4-line-per-row cycle (name+price / seller / buyer / date) churns its NAMES every poll -- not caught by the numeric/date stripping alone, since the names themselves aren't numbers. Two otherwise-identical pages differing only in which names sold must hash the same."""
    assert _stable_content_hash(_NFD_SALES_TABLE) == _stable_content_hash(
        _NFD_SALES_TABLE_DIFFERENT_ROWS
    )


def test_real_change_alongside_activity_table_still_hashes_differently() -> None:
    """A genuine content change elsewhere on the page still registers even when it sits next to a churning activity table -- the table is neutralized, not the whole page."""
    before = "# NFDomains\n\nMint your .algo name today.\n\n" + _NFD_SALES_TABLE
    after = "# NFDomains\n\nNow with vault support for every name.\n\n" + _NFD_SALES_TABLE_DIFFERENT_ROWS
    assert _stable_content_hash(before) != _stable_content_hash(after)


def test_strip_repeating_row_blocks_leaves_prose_and_short_tables_untouched() -> None:
    """A hand-written passage and a small markdown table with genuinely different row content (not just churning identifiers) are never touched -- this must not blank real article content."""
    prose = (
        "## A Decentralized Registry\n\n"
        "NFDomains is Algorand's name service: a permissionless registry that turns\n"
        "opaque addresses into memorable .algo names.\n\n"
        "| Concept | Real-World Implication |\n"
        "| --- | --- |\n"
        "| Forward resolution | Converts a name into an address |\n"
        "| Reverse resolution | Converts an address into a name |\n"
        "| Vaults | Auto opt-in to assets |\n"
    )
    assert _strip_repeating_row_blocks(prose) == prose


def test_strip_repeating_row_blocks_requires_minimum_cycles() -> None:
    """A single occurrence of a multi-line row shape is not enough evidence of a repeating live feed -- left alone.

    Two+ full repeats of this exact 4-line shape total 8+ short-shaped
    lines, which the independent short-run pass has enough evidence to
    catch on its own regardless of exact period -- see
    test_strip_repeating_row_blocks_handles_variable_period_screener for
    that pass's own coverage.
    """
    one_row = "aigirlfriend.algo A 144.34\nnfdomains.algo\ngf.algo\nJul 24, 2026 12:13 AM\n"
    assert _strip_repeating_row_blocks(one_row) == one_row


def test_strip_repeating_row_blocks_handles_variable_period_screener() -> None:
    """hay.app regression pin (2026-08-02): a dense TICKER/PERCENT screener where SOME rows also carry a rank number has no constant period, so the fixed-cycle pass alone misses the tail (found stopping short after the first 3 clean 2-line rows). The independent short-run pass catches the whole thing regardless of exact period."""
    screener = (
        "ORA\n+6%\nALPHA\n+5%\nGONNA\n+2%\nTINY\n-1%\n10\nHOG\n-1%\n11\nFOLKS\n-1%\n12\nHAY\n+3%\n"
    )
    other_values = screener.replace("+6%", "+9%").replace("ORA", "ZETA").replace("10\n", "20\n")
    assert _stable_content_hash(screener) == _stable_content_hash(other_values)
