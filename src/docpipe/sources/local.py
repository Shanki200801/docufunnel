"""Filesystem source. The one adapter that needs no credentials, so it is what
you develop and test pipelines against before pointing them at a mailbox.
"""

from __future__ import annotations

import json
import mimetypes
from collections.abc import Iterator
from pathlib import Path

from ..core import Document, register


@register("source", "local")
class LocalSource:
    def __init__(
        self,
        path: str,
        glob: str = "**/*.pdf",
        state_file: str | None = None,
    ) -> None:
        self.root = Path(path).expanduser()
        self.glob = glob
        # No native "processed" marker on a filesystem, so track seen uids in a
        # JSON file. Committing this file makes re-runs idempotent in CI.
        self.state_path = Path(state_file).expanduser() if state_file else None
        self.seen: set[str] = set()
        if self.state_path and self.state_path.exists():
            self.seen = set(json.loads(self.state_path.read_text()).get("seen", []))

    def fetch(self) -> Iterator[Document]:
        for p in sorted(self.root.glob(self.glob)):
            if not p.is_file():
                continue
            data = p.read_bytes()
            doc = Document(
                filename=p.name,
                data=data,
                mime=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                meta={
                    "source": "local",
                    "path": str(p),
                    "received_at": p.stat().st_mtime,
                },
            )
            if doc.uid in self.seen:
                continue
            yield doc

    def mark_done(self, doc: Document) -> None:
        self.seen.add(doc.uid)
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"seen": sorted(self.seen)}, indent=2))
