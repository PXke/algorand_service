"""stale_deadline_issues: a real, accurately-sourced date framed as still open ("have until", "is set to", ...) when it has already passed by more than the grace window -- root-caused 2026-08-04 (Meld Gold: a June 29, 2026 withdrawal cutoff published over 5 weeks later, still framed as an open deadline)."""

from datetime import date

from app.modules.newspaper.stale_deadline_gate import stale_deadline_issues

_TODAY = date(2026, 8, 4)


def test_flags_a_lapsed_deadline_framed_as_open() -> None:
    """The exact Meld Gold shape: 'have until <past date>' fires, naming the date, the age, and the offending sentence."""
    body = (
        "Meld Gold is phasing out its legacy certificates. Holders have until 4:00pm "
        "(AEST) on June 29, 2026, to withdraw their tokens to an external wallet."
    )
    issues = stale_deadline_issues(body, today=_TODAY)
    assert len(issues) == 1
    assert "2026-06-29" in issues[0]
    assert "36 days ago" in issues[0]


def test_does_not_flag_plain_retrospective_dates() -> None:
    """A past date with no still-open framing is exactly the correct way to describe history -- must never fire."""
    body = (
        "The notice was issued on 23 June 2025. The delisting took effect after "
        "June 24, 2025."
    )
    assert stale_deadline_issues(body, today=_TODAY) == []


def test_does_not_flag_a_genuinely_upcoming_deadline() -> None:
    """A still-open phrase attached to a date that HASN'T passed yet is exactly correct usage."""
    body = "Holders have until 4:00pm (AEST) on December 1, 2026, to withdraw their tokens."
    assert stale_deadline_issues(body, today=_TODAY) == []


def test_grace_window_absorbs_near_term_dates() -> None:
    """A date within the grace window (same-week/timezone-rounding territory) is not flagged as a hard contradiction."""
    body = "Holders have until 4:00pm (AEST) on August 1, 2026, to withdraw their tokens."
    assert stale_deadline_issues(body, today=_TODAY) == []


def test_still_open_phrase_without_any_date_is_a_noop() -> None:
    """The phrase alone, with no parseable date in the sentence, has nothing to contradict."""
    body = "Holders have until the announced cutoff to withdraw their tokens."
    assert stale_deadline_issues(body, today=_TODAY) == []


def test_past_date_without_still_open_phrase_is_a_noop() -> None:
    """A stale date with ordinary prose (no trigger phrase) is not this gate's concern."""
    body = "The token launched on January 5, 2026."
    assert stale_deadline_issues(body, today=_TODAY) == []


def test_multiple_offending_sentences_each_reported() -> None:
    """Two independent still-open+lapsed-date sentences each produce their own issue."""
    body = (
        "Holders have until June 29, 2026, to withdraw their tokens. Separately, "
        "the program is set to conclude on July 1, 2026, pending final review."
    )
    issues = stale_deadline_issues(body, today=_TODAY)
    assert len(issues) == 2


def test_empty_body_is_a_noop() -> None:
    """An empty or missing body short-circuits before any parsing."""
    assert stale_deadline_issues("", today=_TODAY) == []
    assert stale_deadline_issues(None, today=_TODAY) == []  # type: ignore[arg-type]
