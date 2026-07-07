from app.modules.newspaper.ingest_signal import _stable_content_hash


def test_date_only_change_hashes_the_same():
    before = "ZK ColorSort daily puzzle. Last updated: June 18, 2026."
    after = "ZK ColorSort daily puzzle. Last updated: July 6, 2026."
    assert _stable_content_hash(before) == _stable_content_hash(after)


def test_iso_date_only_change_hashes_the_same():
    before = "Snapshot generated 2026-06-18."
    after = "Snapshot generated 2026-07-06."
    assert _stable_content_hash(before) == _stable_content_hash(after)


def test_real_text_change_still_hashes_differently():
    before = "ZK ColorSort daily puzzle. Last updated: June 18, 2026."
    after = "ZK ColorSort now supports multiplayer mode. Last updated: June 18, 2026."
    assert _stable_content_hash(before) != _stable_content_hash(after)
