"""IMAP source — the adapter that makes this tool distributable.

The Gmail API needs a Google Cloud project and the `gmail.modify` restricted
scope, which cannot be shipped as one shared OAuth app: Google requires
verification plus an annual CASA security assessment for that, and an app left
in Testing gets a limited refresh-token lifetime that a cron job cannot live
with. Every user would have to build their own Cloud project.

IMAP needs none of that. On Gmail: enable 2FA, generate an app password, done.
No Cloud project, no consent screen, no verification, no token expiry.

What is given up versus the Gmail API is the label write. Instead this marks
processed mail with an IMAP keyword (which Gmail surfaces as a label) or moves
it to another mailbox. Gmail's own search syntax is still available through
the X-GM-RAW extension, so `gmail_search` accepts the same queries as the
Gmail source.
"""

from __future__ import annotations

import email
import imaplib
import json
import logging
from collections.abc import Iterator
from email.message import Message
from email.utils import parsedate_to_datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ..core import Document, register

log = logging.getLogger("docufunnel.imap")

# Gmail rejects a login with the account password and points at app passwords;
# the raw IMAP error is unhelpful, so it is translated.
_APP_PASSWORD_HINT = (
    "IMAP login failed. On Gmail a normal account password will not work: turn on "
    "2-Step Verification, then create an app password at "
    "https://myaccount.google.com/apppasswords and use that as the password."
)


@register("source", "imap")
class ImapSource:
    def __init__(
        self,
        user: str,
        password: str,
        host: str = "imap.gmail.com",
        port: int = 993,
        mailbox: str = "INBOX",
        # Raw IMAP search criteria, e.g. 'UNSEEN SINCE 01-Aug-2026'.
        search: str = "ALL",
        # Gmail search syntax via the X-GM-RAW extension. Takes precedence over
        # `search` when set, and is what you want on Gmail.
        gmail_search: str | None = None,
        filename_glob: str = "*",
        max_messages: int = 100,
        min_bytes: int = 1024,
        # How a processed message is remembered: an IMAP keyword (a Gmail
        # label), a move to another mailbox, the \\Seen flag, or nothing.
        processed: str = "keyword",
        processed_keyword: str = "docufunnel-done",
        move_to: str = "Processed",
        # Belt-and-braces local dedupe, for servers whose keywords do not stick.
        state_file: str | None = None,
    ) -> None:
        if processed not in ("keyword", "move", "seen", "none"):
            raise ValueError(
                f"processed must be keyword|move|seen|none, got {processed!r}"
            )
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.mailbox = mailbox
        self.search = search
        self.gmail_search = gmail_search
        self.filename_glob = filename_glob
        self.max_messages = max_messages
        self.min_bytes = min_bytes
        self.processed = processed
        self.processed_keyword = processed_keyword
        self.move_to = move_to
        self.state_path = Path(state_file).expanduser() if state_file else None
        self.seen: set[str] = set()
        if self.state_path and self.state_path.exists():
            self.seen = set(json.loads(self.state_path.read_text()).get("seen", []))
        self._conn: Any = None

    # -- connection ---------------------------------------------------------

    def _connect(self) -> Any:
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            conn.login(self.user, self.password)
        except imaplib.IMAP4.error as exc:
            raise RuntimeError(f"{_APP_PASSWORD_HINT} (server said: {exc})") from exc
        return conn

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
            self._conn.select(self.mailbox)
        return self._conn

    # -- search -------------------------------------------------------------

    def _search_args(self) -> tuple[Any, ...]:
        """Build the UID SEARCH arguments, excluding already-processed mail
        server-side so it is never downloaded.
        """
        if self.gmail_search:
            q = self.gmail_search
            if self.processed == "keyword":
                q = f"{q} -label:{self.processed_keyword}"
            elif self.processed == "seen":
                q = f"{q} is:unread"
            return (None, "X-GM-RAW", f'"{q}"')

        criteria = self.search
        if self.processed == "keyword":
            criteria = f"{criteria} UNKEYWORD {self.processed_keyword}"
        elif self.processed == "seen":
            criteria = f"{criteria} UNSEEN"
        return (None, *criteria.split())

    def _uids(self) -> list[bytes]:
        typ, data = self.conn.uid("SEARCH", *self._search_args())
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ} {data}")
        uids = (data[0] or b"").split()
        # Newest last from the server; process oldest first so a partial run
        # leaves a contiguous unprocessed tail.
        return uids[: self.max_messages]

    # -- parsing ------------------------------------------------------------

    @staticmethod
    def _received_at(msg: Message) -> int:
        raw = msg.get("Date")
        if not raw:
            return 0
        try:
            return int(parsedate_to_datetime(raw).timestamp())
        except (TypeError, ValueError):
            return 0

    def _attachments(self, msg: Message) -> Iterator[tuple[str, bytes, str]]:
        for part in msg.walk():
            if part.is_multipart():
                continue
            name = part.get_filename()
            if not name:
                continue
            if not fnmatch(name.lower(), self.filename_glob.lower()):
                continue
            payload = part.get_payload(decode=True)
            if not payload or len(payload) < self.min_bytes:
                continue
            yield name, payload, part.get_content_type()

    def fetch(self) -> Iterator[Document]:
        for uid in self._uids():
            typ, data = self.conn.uid("FETCH", uid, "(RFC822)")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                log.warning("could not fetch uid %s", uid.decode())
                continue
            msg = email.message_from_bytes(data[0][1])
            base_meta = {
                "source": "imap",
                "imap_uid": uid.decode(),
                "mailbox": self.mailbox,
                "sender": str(msg.get("From", "")),
                "subject": str(msg.get("Subject", "")),
                "received_at": self._received_at(msg),
            }
            for name, payload, mime in self._attachments(msg):
                doc = Document(
                    filename=name, data=payload, mime=mime, meta=dict(base_meta)
                )
                if doc.uid in self.seen:
                    continue
                yield doc

    # -- bookkeeping --------------------------------------------------------

    def mark_done(self, doc: Document) -> None:
        self.seen.add(doc.uid)
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"seen": sorted(self.seen)}, indent=2))

        uid = doc.meta.get("imap_uid")
        if not uid or self.processed == "none":
            return

        if self.processed == "keyword":
            self.conn.uid("STORE", uid, "+FLAGS", f"({self.processed_keyword})")
        elif self.processed == "seen":
            self.conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        elif self.processed == "move":
            typ, _ = self.conn.uid("MOVE", uid, self.move_to)
            if typ != "OK":
                # UID MOVE is RFC 6851 and not universal; fall back to the
                # copy-then-delete sequence it replaced.
                self.conn.uid("COPY", uid, self.move_to)
                self.conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                self.conn.expunge()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
                self._conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            self._conn = None
