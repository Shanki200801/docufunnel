"""Gmail source: search, pull attachments, label the thread when done.

Replaces the Apps Script original. Two behavioural differences worth knowing:

* The label is applied per message, not per thread, and only after the sink
  write. A reply arriving later on a processed thread is therefore still
  picked up, which the thread-level version missed.
* The search query is narrowed with `-label:<processed>` so already-handled
  mail is excluded server-side instead of fetched and discarded.
"""

from __future__ import annotations

import base64
import fnmatch
from collections.abc import Iterator
from typing import Any

from ..core import Document, register
from ..google_auth import service


@register("source", "gmail")
class GmailSource:
    def __init__(
        self,
        query: str,
        processed_label: str = "docpipe/processed",
        filename_glob: str = "*",
        max_threads: int = 100,
        min_bytes: int = 1024,
    ) -> None:
        self.query = query
        self.label_name = processed_label
        self.filename_glob = filename_glob
        self.max_threads = max_threads
        # Inline signature images and tracking pixels arrive as attachments;
        # a size floor is the cheapest way to drop them.
        self.min_bytes = min_bytes
        self._label_id: str | None = None

    @property
    def api(self) -> Any:
        return service("gmail", "v1")

    def _ensure_label(self) -> str:
        if self._label_id:
            return self._label_id
        labels = self.api.users().labels().list(userId="me").execute().get("labels", [])
        for lab in labels:
            if lab["name"] == self.label_name:
                self._label_id = lab["id"]
                return self._label_id
        created = (
            self.api.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": self.label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        self._label_id = created["id"]
        return self._label_id

    def _search(self) -> Iterator[str]:
        q = f"{self.query} -label:{self.label_name}"
        page_token = None
        seen = 0
        while True:
            resp = (
                self.api.users()
                .messages()
                .list(
                    userId="me",
                    q=q,
                    maxResults=min(100, self.max_threads - seen),
                    pageToken=page_token,
                )
                .execute()
            )
            for msg in resp.get("messages", []):
                yield msg["id"]
                seen += 1
                if seen >= self.max_threads:
                    return
            page_token = resp.get("nextPageToken")
            if not page_token:
                return

    @staticmethod
    def _headers(payload: dict[str, Any]) -> dict[str, str]:
        return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    @staticmethod
    def _walk(part: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield part
        for sub in part.get("parts", []) or []:
            yield from GmailSource._walk(sub)

    def fetch(self) -> Iterator[Document]:
        self._ensure_label()
        for msg_id in self._search():
            msg = (
                self.api.users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
            payload = msg.get("payload", {})
            hdrs = self._headers(payload)
            base_meta = {
                "source": "gmail",
                "message_id": msg_id,
                "thread_id": msg.get("threadId"),
                "sender": hdrs.get("from", ""),
                "subject": hdrs.get("subject", ""),
                "received_at": int(msg.get("internalDate", 0)) // 1000,
            }

            for part in self._walk(payload):
                name = part.get("filename") or ""
                if not name or not fnmatch.fnmatch(name.lower(), self.filename_glob.lower()):
                    continue
                body = part.get("body", {})
                att_id = body.get("attachmentId")
                if not att_id:
                    continue
                if body.get("size", 0) < self.min_bytes:
                    continue
                att = (
                    self.api.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=msg_id, id=att_id)
                    .execute()
                )
                data = base64.urlsafe_b64decode(att["data"])
                if len(data) < self.min_bytes:
                    continue
                yield Document(
                    filename=name,
                    data=data,
                    mime=part.get("mimeType", "application/octet-stream"),
                    meta={**base_meta, "attachment_id": att_id},
                )

    def mark_done(self, doc: Document) -> None:
        msg_id = doc.meta.get("message_id")
        if not msg_id:
            return
        self.api.users().messages().modify(
            userId="me", id=msg_id, body={"addLabelIds": [self._ensure_label()]}
        ).execute()
