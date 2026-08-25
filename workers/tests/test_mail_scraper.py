r"""IMAP mechanics for the mail inbox scraper.

Covers the fix for a real bug: fetching a message with `(RFC822)` implicitly
marks it `\\Seen` on the server the instant it's fetched, regardless of
whether it's ever successfully processed -- so a mid-batch failure in the
caller's processing (e.g. `ingest_publish_signal` raising) would silently
lose every already-fetched-but-unprocessed message forever. The fix switches
to `BODY.PEEK[]` (no implicit `\\Seen`) and only marks a message `\\Seen`,
via an explicit `STORE`, after its callback reports success.
"""

from __future__ import annotations

import imaplib

import pytest

from app.modules.scraper.core import mail_scraper


class FakeIMAPClient:
    """Stand-in for imaplib.IMAP4_SSL -- no real socket, records every call."""

    def __init__(self, messages: dict[bytes, bytes], *, fail_on: str | None = None) -> None:
        """Seed the fake mailbox; `fail_on` names a method to raise from, to exercise error paths."""
        # uid (bytes) -> raw RFC822 bytes for that message.
        self._messages = messages
        # Name of a method to make raise, to exercise error/logout paths.
        self._fail_on = fail_on
        self.fetch_specs: list[tuple[bytes, str]] = []
        self.store_calls: list[tuple[bytes, str, str]] = []
        self.logout_called = False
        self.selected_folder: str | None = None

    def _maybe_fail(self, name: str) -> None:
        if self._fail_on == name:
            raise imaplib.IMAP4.error(f"synthetic failure in {name}")

    def login(self, _user: str, _password: str) -> tuple[str, list[bytes]]:
        """Fake successful IMAP LOGIN (or raise, if configured to fail here)."""
        self._maybe_fail("login")
        return ("OK", [b"LOGIN completed"])

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        """Fake successful IMAP SELECT, recording the folder name."""
        self._maybe_fail("select")
        self.selected_folder = folder
        return ("OK", [b"1"])

    def search(self, _charset: None, *criteria: str) -> tuple[str, list[bytes]]:
        """Fake IMAP SEARCH, returning every configured message's uid for UNSEEN."""
        self._maybe_fail("search")
        assert criteria == ("UNSEEN",)
        uids = b" ".join(sorted(self._messages.keys(), key=lambda u: int(u)))
        return ("OK", [uids])

    def fetch(self, uid: bytes, spec: str) -> tuple[str, list[object]]:
        """Fake IMAP FETCH, recording the exact spec used (asserted to be BODY.PEEK[])."""
        self._maybe_fail("fetch")
        self.fetch_specs.append((uid, spec))
        if uid not in self._messages:
            return ("OK", [None])
        header = f"{uid.decode()} (BODY[] {{{len(self._messages[uid])}}}".encode()
        return ("OK", [(header, self._messages[uid])])

    def store(self, uid: bytes, flags_cmd: str, flags: str) -> tuple[str, list[bytes]]:
        """Fake IMAP STORE, recording every flag-change call for assertions."""
        self._maybe_fail("store")
        self.store_calls.append((uid, flags_cmd, flags))
        return ("OK", [b"1 (FLAGS (\\Seen))"])

    def logout(self) -> tuple[str, list[bytes]]:
        """Fake IMAP LOGOUT, recording that it was called."""
        self.logout_called = True
        self._maybe_fail("logout")
        return ("BYE", [b"logging out"])


def _raw_message(*, from_: str = "sender@example.com", subject: str = "Hi", body: str = "Body") -> bytes:
    return (
        f"From: {from_}\r\nSubject: {subject}\r\nContent-Type: text/plain\r\n\r\n{body}"
    ).encode()


@pytest.fixture(autouse=True)
def _configure_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point mail_scraper at a fake host so the early empty-config return doesn't fire."""
    monkeypatch.setattr(mail_scraper, "MAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setattr(mail_scraper, "MAIL_IMAP_USER", "bot@example.com")
    monkeypatch.setattr(mail_scraper, "MAIL_IMAP_PASSWORD", "secret")
    monkeypatch.setattr(mail_scraper, "MAIL_IMAP_PORT", 993)
    monkeypatch.setattr(mail_scraper, "MAIL_IMAP_FOLDER", "INBOX")


def _install_client(monkeypatch: pytest.MonkeyPatch, client: FakeIMAPClient) -> None:
    monkeypatch.setattr(mail_scraper.imaplib, "IMAP4_SSL", lambda _host, _port: client)


def test_successful_message_is_fetched_via_peek_and_marked_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""A message whose callback succeeds is fetched with BODY.PEEK[] (not RFC822) and then explicitly marked \\Seen."""
    client = FakeIMAPClient({b"1": _raw_message(subject="Ann")})
    _install_client(monkeypatch, client)

    seen: list[dict[str, str]] = []
    result = mail_scraper.poll_unread_messages(limit=20, on_message=lambda msg: seen.append(msg) or True)

    assert [m["subject"] for m in result] == ["Ann"]
    assert client.fetch_specs == [(b"1", "(BODY.PEEK[])")]
    assert client.store_calls == [(b"1", "+FLAGS", "\\Seen")]
    assert seen == result


def test_failed_message_is_not_marked_seen_and_not_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message whose callback returns False is left unseen (no STORE call) so a future poll picks it up again."""
    client = FakeIMAPClient({b"1": _raw_message(subject="Fails")})
    _install_client(monkeypatch, client)

    result = mail_scraper.poll_unread_messages(limit=20, on_message=lambda _msg: False)

    assert len(result) == 1  # still fetched/attempted
    assert client.store_calls == []  # never marked \Seen


def test_callback_raising_is_caught_and_message_left_unseen(monkeypatch: pytest.MonkeyPatch) -> None:
    """If on_message raises outright (caller didn't catch its own error), the message still isn't marked seen and the exception doesn't escape."""
    client = FakeIMAPClient({b"1": _raw_message(subject="Boom")})
    _install_client(monkeypatch, client)

    def _boom(_msg: dict[str, str]) -> bool:
        raise RuntimeError("ingest exploded")

    result = mail_scraper.poll_unread_messages(limit=20, on_message=_boom)

    assert len(result) == 1
    assert client.store_calls == []
    assert client.logout_called


def test_other_messages_in_batch_still_processed_after_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One message's callback failure doesn't stop the rest of the batch from being attempted."""
    client = FakeIMAPClient(
        {
            b"1": _raw_message(subject="First"),
            b"2": _raw_message(subject="Second"),
            b"3": _raw_message(subject="Third"),
        }
    )
    _install_client(monkeypatch, client)

    attempted: list[str] = []

    def _process(msg: dict[str, str]) -> bool:
        attempted.append(msg["subject"])
        if msg["subject"] == "Second":
            raise RuntimeError("boom on second")
        return True

    result = mail_scraper.poll_unread_messages(limit=20, on_message=_process)

    assert attempted == ["First", "Second", "Third"]
    assert [m["subject"] for m in result] == ["First", "Second", "Third"]
    assert client.store_calls == [(b"1", "+FLAGS", "\\Seen"), (b"3", "+FLAGS", "\\Seen")]


@pytest.mark.parametrize("fail_on", ["login", "select", "search", "fetch"])
def test_connection_is_always_logged_out_even_on_error(
    monkeypatch: pytest.MonkeyPatch, fail_on: str
) -> None:
    """logout() is always attempted, even when an earlier IMAP call raises."""
    client = FakeIMAPClient({b"1": _raw_message()}, fail_on=fail_on)
    _install_client(monkeypatch, client)

    with pytest.raises(imaplib.IMAP4.error):
        mail_scraper.poll_unread_messages(limit=20, on_message=lambda _msg: True)

    assert client.logout_called


def test_no_unread_messages_returns_empty_without_fetching(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty UNSEEN search short-circuits without ever calling fetch, and still logs out."""
    client = FakeIMAPClient({})
    _install_client(monkeypatch, client)

    called = False

    def _process(_msg: dict[str, str]) -> bool:
        nonlocal called
        called = True
        return True

    result = mail_scraper.poll_unread_messages(limit=20, on_message=_process)

    assert result == []
    assert not called
    assert client.fetch_specs == []
    assert client.logout_called


def test_missing_config_returns_empty_without_opening_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No IMAP host/user configured -> no connection is attempted at all."""
    monkeypatch.setattr(mail_scraper, "MAIL_IMAP_HOST", "")

    def _boom_ctor(_host: str, _port: int) -> FakeIMAPClient:
        raise AssertionError("should not construct an IMAP client when unconfigured")

    monkeypatch.setattr(mail_scraper.imaplib, "IMAP4_SSL", _boom_ctor)

    assert mail_scraper.poll_unread_messages(limit=20, on_message=lambda _msg: True) == []
