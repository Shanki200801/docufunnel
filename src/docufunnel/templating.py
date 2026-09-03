"""Minimal {{var}} substitution for config strings — Drive folder paths, sheet
tab names, output filenames.

Deliberately not Jinja: config authors should not be able to embed logic in a
path, and the dependency is not worth it for this.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .core import Document

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def context(doc: Document) -> dict[str, str]:
    """Template vars for a document. Falls back to now() when the source did
    not supply a timestamp, so a path never renders with an empty segment.
    """
    ts = doc.meta.get("received_at")
    when = (
        datetime.fromtimestamp(float(ts), tz=UTC)
        if isinstance(ts, (int, float))
        else datetime.now(tz=UTC)
    )
    ctx: dict[str, Any] = {
        "year": f"{when:%Y}",
        "month": f"{when:%m}",
        "day": f"{when:%d}",
        "date": f"{when:%Y-%m-%d}",
        "yyyymm": f"{when:%Y-%m}",
        "filename": doc.filename,
        "stem": doc.filename.rsplit(".", 1)[0],
        "uid": doc.uid,
    }
    for k, v in doc.meta.items():
        ctx.setdefault(k, v)
    return {k: str(v) for k, v in ctx.items()}


def render(template: str, doc: Document) -> str:
    ctx = context(doc)
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in ctx:
            missing.append(key)
            return ""
        return ctx[key]

    out = _VAR_RE.sub(sub, template)
    if missing:
        raise KeyError(
            f"template {template!r} references unknown var(s) {sorted(set(missing))}; "
            f"available: {sorted(ctx)}"
        )
    return out
