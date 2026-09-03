"""End-to-end tests over the credential-free path: local -> local ->
markitdown -> regex -> csv.

This path exercises the orchestrator, the registry, templating, the fallback
mechanism and sink dedupe without needing a Google or Gemini credential.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from docufunnel import Pipeline, config
from docufunnel.core import Document, register, resolve


def _cfg(tmp_path: Path, inbox: Path, **over) -> dict:
    base = {
        "name": "test",
        "source": {"type": "local", "path": str(inbox), "glob": "*.pdf"},
        "store": {"type": "local", "path": str(tmp_path / "archive/{{yyyymm}}")},
        "normalize": {"type": "markitdown"},
        "extract": {
            "type": "regex",
            "fields": {
                "invoice_no": r"Invoice\s*#:\s*(\S+)",
                # Anchored: an unanchored `Total:` also matches "Subtotal:",
                # which is exactly the brittleness the llm extractor avoids.
                "total": {"pattern": r"^Total:\s*([\d,.]+)", "cast": "number"},
            },
        },
        "sink": {"type": "csv", "path": str(tmp_path / "out.csv"), "dedupe_key": "_uid"},
    }
    base.update(over)
    return base


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "pipe.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_end_to_end_extracts_and_archives(tmp_path: Path, inbox: Path) -> None:
    cfg = config.load(_write(tmp_path, _cfg(tmp_path, inbox)))
    res = Pipeline(cfg).run()

    assert res.fetched == 2
    assert res.stored == 2
    # Only the text-layer PDF normalizes; the blank one yields no text and its
    # regex extraction raises, so it lands in skipped rather than aborting.
    assert res.normalized == 1
    assert len(res.skipped) == 1

    rows = list(csv.DictReader((tmp_path / "out.csv").open()))
    assert len(rows) == 1
    assert rows[0]["invoice_no"] == "INV-2026-0042"
    assert float(rows[0]["total"]) == 162.84
    assert rows[0]["_stored_uri"].endswith("invoice-0042.pdf")

    archived = list((tmp_path / "archive").rglob("*.pdf"))
    assert len(archived) == 2


def test_rerun_is_idempotent(tmp_path: Path, inbox: Path) -> None:
    """Dedupe and source state must both hold, since either alone leaves a hole."""
    state = tmp_path / "state.json"
    data = _cfg(tmp_path, inbox)
    data["source"]["state_file"] = str(state)
    path = _write(tmp_path, data)

    first = Pipeline(config.load(path)).run()
    assert first.written == 1

    second = Pipeline(config.load(path)).run()
    # The document that succeeded was marked done and is filtered out at the
    # source. The one that failed was never marked, so it is retried — a
    # transient failure must not be permanently swallowed.
    assert second.fetched == 1
    assert [name for name, _ in second.skipped] == ["scanned.pdf"]
    assert second.written == 0

    rows = list(csv.DictReader((tmp_path / "out.csv").open()))
    assert len(rows) == 1


def test_dry_run_writes_nothing(tmp_path: Path, inbox: Path) -> None:
    data = _cfg(tmp_path, inbox)
    data["dry_run"] = True
    res = Pipeline(config.load(_write(tmp_path, data))).run()

    assert res.fetched == 2
    assert res.stored == 0
    assert not (tmp_path / "out.csv").exists()
    assert not (tmp_path / "archive").exists()


def test_fallback_fires_when_primary_returns_too_little(tmp_path: Path, inbox: Path) -> None:
    """The scanned-PDF case: primary succeeds with near-empty text, so the
    fallback must be chosen on output length, not on an exception.
    """

    @register("normalize", "_stub_ocr")
    class StubOcr:
        calls: ClassVar[list[str]] = []

        def to_text(self, doc: Document) -> str:
            StubOcr.calls.append(doc.filename)
            return "Invoice #: INV-OCR-1\nTotal: 99.00\n" + "x" * 300

    resolve("normalize", "_stub_ocr").calls.clear()

    data = _cfg(tmp_path, inbox)
    data["normalize"] = {
        "type": "markitdown",
        "fallback": {"type": "_stub_ocr", "min_text_len": 200},
    }
    res = Pipeline(config.load(_write(tmp_path, data))).run()

    calls = resolve("normalize", "_stub_ocr").calls
    assert "scanned.pdf" in calls, "fallback did not fire on the text-free PDF"
    assert res.written == 2
    invoice_nos = {
        r["invoice_no"] for r in csv.DictReader((tmp_path / "out.csv").open())
    }
    assert invoice_nos == {"INV-2026-0042", "INV-OCR-1"}


def test_env_interpolation_and_missing_var(tmp_path: Path, inbox: Path, monkeypatch) -> None:
    data = _cfg(tmp_path, inbox)
    data["sink"] = {"type": "jsonl", "path": "${OUT_PATH}", "dedupe_key": "_uid"}
    path = _write(tmp_path, data)

    with pytest.raises(config.MissingEnvVar, match="OUT_PATH"):
        config.load(path)

    monkeypatch.setenv("OUT_PATH", str(tmp_path / "records.jsonl"))
    Pipeline(config.load(path)).run()
    lines = (tmp_path / "records.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["invoice_no"] == "INV-2026-0042"


def test_extract_without_sink_is_rejected(tmp_path: Path, inbox: Path) -> None:
    data = _cfg(tmp_path, inbox)
    data.pop("sink")
    with pytest.raises(ValueError, match="sink"):
        config.load(_write(tmp_path, data))


def test_unknown_adapter_lists_alternatives(tmp_path: Path, inbox: Path) -> None:
    data = _cfg(tmp_path, inbox)
    data["sink"] = {"type": "nope"}
    with pytest.raises(KeyError, match="csv"):
        Pipeline(config.load(_write(tmp_path, data)))


def test_csv_sink_widens_header_without_reordering(tmp_path: Path, inbox: Path) -> None:
    from docufunnel.sinks.csv_ import CsvSink

    out = tmp_path / "wide.csv"
    sink = CsvSink(path=str(out))

    d1 = Document(filename="a.pdf", data=b"a")
    d1.records = [{"b": 1, "a": 2}]
    sink.write([d1])

    d2 = Document(filename="b.pdf", data=b"b")
    d2.records = [{"a": 3, "c": 4}]
    sink.write([d2])

    rows = list(csv.DictReader(out.open()))
    assert list(rows[0]) == ["b", "a", "c"]
    assert rows[1]["c"] == "4"
    assert rows[0]["c"] == ""
