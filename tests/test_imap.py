"""IMAP source tests against a fake connection.

The IMAP source is the adapter most users will actually run, so its search
construction and attachment walking are worth pinning down without a live
server.
"""

from __future__ import annotations

import imaplib
from email.message import EmailMessage
from typing import Any

import pytest

from docufunnel.sources.imap import ImapSource


def _message(
    *,
    subject: str = "Invoice 42",
    sender: str = "billing@acme.test",
    date: str = "Fri, 14 Aug 2026 10:00:00 +0000",
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "me@example.test"
    msg["Date"] = date
    msg.set_content("see attached")
    for name, data, subtype in attachments or []:
        msg.add_attachment(
            data, maintype="application", subtype=subtype, filename=name
        )
    return msg.as_bytes()


class FakeImap:
    """Records every uid() command so tests can assert on the wire protocol."""

    def __init__(self, uids: list[bytes], messages: dict[bytes, bytes]) -> None:
        self.uids = uids
        self.messages = messages
        self.calls: list[tuple[Any, ...]] = []
        self.selected: str | None = None
        self.move_ok = True
        self.expunged = False

    def select(self, mailbox: str) -> tuple[str, list]:
        self.selected = mailbox
        return ("OK", [b"1"])

    def uid(self, command: str, *args: Any) -> tuple[str, list]:
        self.calls.append((command, *args))
        if command == "SEARCH":
            return ("OK", [b" ".join(self.uids)])
        if command == "FETCH":
            uid = args[0]
            return ("OK", [(b"1 (RFC822 {n}", self.messages[uid])])
        if command == "MOVE":
            return ("OK" if self.move_ok else "NO", [b""])
        return ("OK", [b""])

    def expunge(self) -> tuple[str, list]:
        self.expunged = True
        return ("OK", [b""])

    def close(self) -> None: ...

    def logout(self) -> None: ...


def _wire(src: ImapSource, fake: FakeImap) -> ImapSource:
    src._connect = lambda: fake  # type: ignore[method-assign]
    return src


PDF = b"%PDF-1.4" + b"x" * 3000
PNG = b"\x89PNG" + b"x" * 200  # under the default min_bytes floor


def test_gmail_search_excludes_the_processed_label() -> None:
    src = ImapSource(user="u", password="p", gmail_search="has:attachment filename:pdf")
    args = src._search_args()
    assert args[1] == "X-GM-RAW"
    # The exclusion must be server-side, or every run re-downloads old mail.
    assert "-label:docufunnel-done" in args[2]
    assert "has:attachment filename:pdf" in args[2]


def test_raw_imap_search_excludes_via_unkeyword() -> None:
    src = ImapSource(user="u", password="p", search="SINCE 01-Aug-2026")
    assert src._search_args() == (None, "SINCE", "01-Aug-2026", "UNKEYWORD", "docufunnel-done")


def test_seen_mode_switches_the_exclusion_term() -> None:
    assert "UNSEEN" in ImapSource(user="u", password="p", processed="seen")._search_args()
    gm = ImapSource(user="u", password="p", processed="seen", gmail_search="x")._search_args()
    assert "is:unread" in gm[2]


def test_none_mode_adds_no_exclusion() -> None:
    src = ImapSource(user="u", password="p", search="ALL", processed="none")
    assert src._search_args() == (None, "ALL")


def test_bad_processed_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="keyword"):
        ImapSource(user="u", password="p", processed="nonsense")


def test_fetch_yields_attachments_with_metadata() -> None:
    raw = _message(attachments=[("invoice.pdf", PDF, "pdf")])
    fake = FakeImap([b"7"], {b"7": raw})
    src = _wire(ImapSource(user="u", password="p", filename_glob="*.pdf"), fake)

    docs = list(src.fetch())
    assert len(docs) == 1
    d = docs[0]
    assert d.filename == "invoice.pdf"
    assert d.data == PDF
    assert d.meta["sender"] == "billing@acme.test"
    assert d.meta["subject"] == "Invoice 42"
    assert d.meta["imap_uid"] == "7"
    # Date header parsed into an epoch the templating layer can use.
    assert d.meta["received_at"] > 0
    assert fake.selected == "INBOX"


def test_fetch_filters_by_glob_and_size() -> None:
    raw = _message(
        attachments=[("invoice.pdf", PDF, "pdf"), ("logo.png", PNG, "png")]
    )
    fake = FakeImap([b"7"], {b"7": raw})

    # The glob rejects the image outright.
    src = _wire(ImapSource(user="u", password="p", filename_glob="*.pdf"), fake)
    assert [d.filename for d in src.fetch()] == ["invoice.pdf"]

    # With the glob open, the size floor still drops a signature-sized image.
    src2 = _wire(ImapSource(user="u", password="p", filename_glob="*"), FakeImap([b"7"], {b"7": raw}))
    assert [d.filename for d in src2.fetch()] == ["invoice.pdf"]


def test_message_without_attachments_yields_nothing() -> None:
    fake = FakeImap([b"1"], {b"1": _message()})
    src = _wire(ImapSource(user="u", password="p"), fake)
    assert list(src.fetch()) == []


def test_max_messages_caps_the_uid_list() -> None:
    uids = [str(i).encode() for i in range(1, 11)]
    msgs = {u: _message(attachments=[("a.pdf", PDF, "pdf")]) for u in uids}
    src = _wire(ImapSource(user="u", password="p", max_messages=3), FakeImap(uids, msgs))
    assert len(list(src.fetch())) == 3


def test_mark_done_sets_the_keyword() -> None:
    raw = _message(attachments=[("a.pdf", PDF, "pdf")])
    fake = FakeImap([b"9"], {b"9": raw})
    src = _wire(ImapSource(user="u", password="p"), fake)
    doc = next(iter(src.fetch()))
    src.mark_done(doc)
    assert ("STORE", "9", "+FLAGS", "(docufunnel-done)") in fake.calls


def test_mark_done_move_falls_back_to_copy_and_delete() -> None:
    raw = _message(attachments=[("a.pdf", PDF, "pdf")])
    fake = FakeImap([b"9"], {b"9": raw})
    fake.move_ok = False  # server without RFC 6851 UID MOVE
    src = _wire(ImapSource(user="u", password="p", processed="move", move_to="Done"), fake)
    src.mark_done(next(iter(src.fetch())))

    commands = [c[0] for c in fake.calls]
    assert "MOVE" in commands and "COPY" in commands
    assert ("STORE", "9", "+FLAGS", "(\\Deleted)") in fake.calls
    assert fake.expunged


def test_state_file_survives_a_restart(tmp_path) -> None:
    raw = _message(attachments=[("a.pdf", PDF, "pdf")])
    state = tmp_path / "s.json"

    src = _wire(ImapSource(user="u", password="p", state_file=str(state)), FakeImap([b"9"], {b"9": raw}))
    doc = next(iter(src.fetch()))
    src.mark_done(doc)

    # A fresh instance reads the state back and skips the same attachment even
    # though the server would still offer it.
    src2 = _wire(ImapSource(user="u", password="p", state_file=str(state)), FakeImap([b"9"], {b"9": raw}))
    assert list(src2.fetch()) == []


def test_login_failure_explains_app_passwords(monkeypatch) -> None:
    def boom(self, *a, **k):
        raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", lambda self, h, p: None)
    monkeypatch.setattr(imaplib.IMAP4_SSL, "login", boom)

    with pytest.raises(RuntimeError, match="app password"):
        ImapSource(user="u", password="p")._connect()
