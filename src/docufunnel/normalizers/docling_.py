"""Docling normalizer — the heavy, accurate alternative to MarkItDown.

Use it as the `fallback` when documents are scanned or table-dense: Docling
runs real layout and table-structure models and can OCR, so it recovers pages
MarkItDown returns blank. The cost is model downloads on first use and
seconds-per-page instead of milliseconds, which is why it is not the default.
"""

from __future__ import annotations

import io

from ..core import Document, register
from ..deps import missing


@register("normalize", "docling")
class DoclingNormalizer:
    def __init__(self, ocr: bool = True, table_mode: str = "accurate") -> None:
        self.ocr = ocr
        self.table_mode = table_mode
        self._conv = None

    def _converter(self):
        if self._conv is not None:
            return self._conv
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                TableFormerMode,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:
            raise missing("docling", "docling", "the OCR / table fallback") from exc

        opts = PdfPipelineOptions()
        opts.do_ocr = self.ocr
        opts.do_table_structure = True
        opts.table_structure_options.mode = (
            TableFormerMode.ACCURATE
            if self.table_mode == "accurate"
            else TableFormerMode.FAST
        )
        self._conv = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        return self._conv

    def to_text(self, doc: Document) -> str | None:
        # Build the converter first: it raises the error that names the extra,
        # which a bare `import docling...` here would pre-empt with an
        # unhelpful ModuleNotFoundError.
        converter = self._converter()
        from docling.datamodel.base_models import DocumentStream

        source = DocumentStream(name=doc.filename, stream=io.BytesIO(doc.data))
        result = converter.convert(source)
        return (result.document.export_to_markdown() or "").strip() or None
