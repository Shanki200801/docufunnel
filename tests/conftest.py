from __future__ import annotations

from pathlib import Path

import pytest

INVOICE_TEXT = [
    "ACME SUPPLIES LTD",
    "Invoice #: INV-2026-0042",
    "Invoice Date: 2026-08-14",
    "Bill To: Shashank",
    "",
    "Description            Qty    Unit Price    Amount",
    "Widget, large           10         12.50    125.00",
    "Widget, small           4           3.25     13.00",
    "",
    "Subtotal: 138.00",
    "Tax (18%): 24.84",
    "Total: 162.84 USD",
]


def _write_pdf(path: Path, lines: list[str]) -> None:
    """Build a text-layer PDF without a rendering dependency.

    Hand-rolled rather than reportlab/fpdf2 so the test suite has no extra
    install, and so the fixture is a genuine PDF that pdfminer parses (not a
    stub that would let a broken normalizer pass).
    """
    body = "BT /F1 11 Tf 14 TL 40 760 Td\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        body += f"({escaped}) Tj T*\n"
    body += "ET"
    stream = body.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    """A folder holding one text-layer PDF and one 'scanned' PDF (no text)."""
    d = tmp_path / "inbox"
    d.mkdir()
    _write_pdf(d / "invoice-0042.pdf", INVOICE_TEXT)
    _write_pdf(d / "scanned.pdf", [])
    return d
