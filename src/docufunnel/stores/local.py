"""Filesystem store."""

from __future__ import annotations

from pathlib import Path

from ..core import Document, register
from ..templating import render


@register("store", "local")
class LocalStore:
    def __init__(self, path: str = "out/{{yyyymm}}", overwrite: bool = False) -> None:
        self.path_template = path
        self.overwrite = overwrite

    def put(self, doc: Document) -> str:
        target = Path(render(self.path_template, doc)).expanduser() / doc.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not self.overwrite:
            # Same name, different content: keep both rather than silently lose one.
            target = target.with_name(f"{target.stem}.{doc.uid}{target.suffix}")
        target.write_bytes(doc.data)
        return str(target)
