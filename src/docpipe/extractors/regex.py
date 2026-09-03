"""Deterministic extractors — no API key, no cost, no non-determinism.

Worth keeping alongside the LLM extractor for three reasons: fixed-layout
senders do not need a model, tests need a repeatable extractor, and a regex
result is auditable in a way a model's is not.
"""

from __future__ import annotations

import re
from typing import Any

from ..core import Document, register

_CASTS: dict[str, Any] = {
    "string": lambda s: s.strip(),
    "number": lambda s: float(re.sub(r"[^\d.\-]", "", s)),
    "integer": lambda s: int(re.sub(r"[^\d\-]", "", s)),
}


@register("extract", "regex")
class RegexExtractor:
    def __init__(self, fields: dict[str, Any], flags: str = "im") -> None:
        self.flags = 0
        for ch in flags:
            self.flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}.get(ch, 0)
        self.fields: dict[str, tuple[re.Pattern[str], str, int]] = {}
        for name, spec in fields.items():
            if isinstance(spec, str):
                pattern, cast, group = spec, "string", 1
            else:
                pattern = spec["pattern"]
                cast = spec.get("cast", "string")
                group = spec.get("group", 1)
            if cast not in _CASTS:
                raise ValueError(
                    f"field {name!r}: unknown cast {cast!r}, expected one of {sorted(_CASTS)}"
                )
            self.fields[name] = (re.compile(pattern, self.flags), cast, group)

    def extract(self, doc: Document) -> list[dict[str, Any]]:
        if not doc.text:
            raise ValueError(
                "regex extractor needs text; configure a normalize stage other "
                "than passthrough"
            )
        row: dict[str, Any] = {}
        for name, (pattern, cast, group) in self.fields.items():
            m = pattern.search(doc.text)
            if not m:
                row[name] = None
                continue
            try:
                row[name] = _CASTS[cast](m.group(group))
            except (ValueError, IndexError):
                row[name] = None
        row |= {
            "_source_file": doc.filename,
            "_stored_uri": doc.stored_uri or "",
            "_uid": doc.uid,
        }
        return [row]


@register("extract", "text")
class TextExtractor:
    """Emits the normalised text itself as one record.

    Use it to inspect what a normalizer actually produced before writing a
    schema, and as the extract stage of an archive-only pipeline.
    """

    def __init__(self, max_chars: int = 40_000) -> None:
        self.max_chars = max_chars

    def extract(self, doc: Document) -> list[dict[str, Any]]:
        return [
            {
                "_source_file": doc.filename,
                "_stored_uri": doc.stored_uri or "",
                "_uid": doc.uid,
                "_sender": doc.meta.get("sender", ""),
                "_subject": doc.meta.get("subject", ""),
                "chars": len(doc.text or ""),
                "text": (doc.text or "")[: self.max_chars],
            }
        ]
