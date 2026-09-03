"""MarkItDown normalizer — anything to Markdown.

Chosen as the default because its format coverage is the widest of the free
options: PDF, DOCX, XLSX, PPTX, HTML, CSV, EPub, ZIP, images. The pipeline
therefore is not PDF-only; a vendor who mails a spreadsheet still works.

Its PDF path is pdfminer.six, which means no OCR: a scanned PDF converts
"successfully" to near-empty text. That is why the pipeline supports a
fallback normalizer keyed on output length rather than on exceptions.
"""

from __future__ import annotations

import io
from pathlib import Path

from ..core import Document, register


@register("normalize", "markitdown")
class MarkItDownNormalizer:
    def __init__(self, describe_images: bool = False, model: str | None = None) -> None:
        # MarkItDown can caption images via an LLM client. Off by default: it
        # turns a local, free, fast conversion into a metered API call.
        self.describe_images = describe_images
        self.model = model
        self._md = None

    def _converter(self):
        if self._md is not None:
            return self._md
        from markitdown import MarkItDown

        self._md = MarkItDown(enable_plugins=False)
        return self._md

    def to_text(self, doc: Document) -> str | None:
        md = self._converter()
        ext = Path(doc.filename).suffix or None
        stream = io.BytesIO(doc.data)

        # convert_stream's signature moved from `file_extension=` to a
        # StreamInfo object across 0.0.x releases; support both so the pin can
        # float.
        try:
            from markitdown import StreamInfo  # type: ignore[attr-defined]

            info = StreamInfo(extension=ext, mimetype=doc.mime, filename=doc.filename)
            result = md.convert_stream(stream, stream_info=info)
        except (ImportError, TypeError):
            stream.seek(0)
            result = md.convert_stream(stream, file_extension=ext)

        return (result.text_content or "").strip() or None
