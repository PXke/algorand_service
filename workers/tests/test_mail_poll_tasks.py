r"""poll_mail_inbox orchestration: per-message error isolation.

mail_scraper.poll_unread_messages owns the IMAP mechanics (BODY.PEEK[] fetch,
conditional \\Seen marking -- covered by test_mail_scraper.py). This file
covers the task-layer contract: poll_mail_inbox's `_process` callback must
catch its own `ingest_publish_signal` failures (returning False, never
raising) so one bad message doesn't stop the rest of the batch or blow up
the whole Celery task -- and a failed message must be reported as unseen
back to the (faked) mail_scraper layer.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.modules.newspaper.tasks import mail_poll_tasks


class _FakePoller:
    """Stands in for mail_scraper.poll_unread_messages.

    Drives a canned batch of messages through whatever `on_message` callback
    poll_mail_inbox hands it, the same way the real IMAP-backed one would,
    and records which uids the callback reported success/failure for.
    """

    def __init__(self, messages: list[dict[str, str]]) -> None:
        self._messages = messages
        self.marked_seen: list[str] = []
        self.left_unseen: list[str] = []

    def __call__(
        self, *, limit: int, on_message: Callable[[dict[str, str]], bool]
    ) -> list[dict[str, str]]:
        del limit
        attempted = []
        for msg in self._messages:
            attempted.append(msg)
            try:
                ok = on_message(msg)
            except Exception:
                ok = False
            (self.marked_seen if ok else self.left_unseen).append(msg["uid"])
        return attempted


def _message(uid: str, subject: str = "Subj", from_: str = "a@example.com") -> dict[str, str]:
    return {"uid": uid, "from": from_, "subject": subject, "text": f"body {uid}"}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make poll_mail_inbox pass its two early gates (crawler enabled, host configured)."""
    monkeypatch.setattr(mail_poll_tasks, "mail_crawl_disabled_reason", lambda: None)
    monkeypatch.setattr(mail_poll_tasks, "MAIL_IMAP_HOST", "imap.example.com")


def test_all_messages_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every message's ingest_publish_signal call succeeding marks all of them seen."""
    poller = _FakePoller([_message("1"), _message("2")])
    monkeypatch.setattr(mail_poll_tasks, "poll_unread_messages", poller)
    monkeypatch.setattr(
        mail_poll_tasks,
        "ingest_publish_signal",
        lambda **_kw: {"status": "enqueued"},
    )

    out = mail_poll_tasks.poll_mail_inbox()

    assert out["status"] == "ok"
    assert out["polled"] == 2
    assert poller.marked_seen == ["1", "2"]
    assert poller.left_unseen == []
    assert [r["uid"] for r in out["results"]] == ["1", "2"]


def test_one_message_failing_does_not_lose_or_block_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising ingest_publish_signal for one message is caught, leaves that message unseen (so it's retried later), and doesn't stop the other messages from being attempted."""
    poller = _FakePoller([_message("1"), _message("2"), _message("3")])
    monkeypatch.setattr(mail_poll_tasks, "poll_unread_messages", poller)

    calls: list[str] = []

    def _ingest_fail_middle(**kwargs: object) -> dict[str, str]:
        source_url = kwargs["source_url"]
        calls.append(str(source_url))
        if str(source_url).endswith("/2"):
            raise RuntimeError("synthetic ingest failure")
        return {"status": "enqueued"}

    monkeypatch.setattr(mail_poll_tasks, "ingest_publish_signal", _ingest_fail_middle)

    out = mail_poll_tasks.poll_mail_inbox()

    # All three were attempted despite message 2 failing.
    assert len(calls) == 3
    assert out["polled"] == 3
    assert poller.marked_seen == ["1", "3"]
    assert poller.left_unseen == ["2"]

    results_by_uid = {r["uid"]: r for r in out["results"]}
    assert results_by_uid["1"]["status"] == "enqueued"
    assert results_by_uid["2"]["status"] == "error"
    assert results_by_uid["3"]["status"] == "enqueued"

    # The task itself must not raise/fail even though one message's ingest blew up.
    assert out["status"] == "ok"


def test_skips_when_mail_crawl_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doesn't touch the poller at all when the mail crawler flag is off."""
    monkeypatch.setattr(mail_poll_tasks, "mail_crawl_disabled_reason", lambda: "crawler_mail_disabled")

    def _boom(**_kw: object) -> list[dict[str, str]]:
        raise AssertionError("poll_unread_messages should not be called when disabled")

    monkeypatch.setattr(mail_poll_tasks, "poll_unread_messages", _boom)

    out = mail_poll_tasks.poll_mail_inbox()

    assert out == {"status": "skipped", "reason": "crawler_mail_disabled", "polled": 0}


def test_skips_when_host_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doesn't touch the poller at all when MAIL_IMAP_HOST is unconfigured."""
    monkeypatch.setattr(mail_poll_tasks, "MAIL_IMAP_HOST", "")

    def _boom(**_kw: object) -> list[dict[str, str]]:
        raise AssertionError("poll_unread_messages should not be called when unconfigured")

    monkeypatch.setattr(mail_poll_tasks, "poll_unread_messages", _boom)

    out = mail_poll_tasks.poll_mail_inbox()

    assert out == {"status": "skipped", "reason": "MAIL_IMAP_HOST unset", "polled": 0}
