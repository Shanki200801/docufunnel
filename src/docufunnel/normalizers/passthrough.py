"""No-op normalizer: leaves doc.text as None so the extractor receives the raw
bytes.

This is the right choice with a multimodal extractor. Gemini reads a PDF
natively and sees the layout, column alignment and stamps that a
markdown conversion throws away, and it handles scans without a separate OCR
step. The trade is token cost (~258 tokens per page) versus a few hundred
tokens of extracted markdown.
"""

from __future__ import annotations

from ..core import Document, register


@register("normalize", "passthrough")
class PassthroughNormalizer:
    def to_text(self, doc: Document) -> str | None:
        return None
