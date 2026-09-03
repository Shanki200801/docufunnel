"""Unit tests for the YAML -> JSON-schema compiler used by the llm extractor.

Worth testing offline because a malformed schema fails at API-call time, i.e.
after the pipeline has already spent work fetching and storing a document.
"""

from __future__ import annotations

import pytest

from docpipe.core import Document
from docpipe.extractors.llm import LLMExtractor, compile_schema


def test_shorthand_scalars():
    s = compile_schema({"a": "string", "b": "number", "c": "integer", "d": "bool"})
    assert s["type"] == "object"
    assert s["required"] == ["a", "b", "c", "d"]
    assert s["properties"]["b"]["type"] == "number"
    assert s["properties"]["c"]["type"] == "integer"
    assert s["properties"]["d"]["type"] == "boolean"
    # Nullable so the model can report absence instead of inventing a value.
    assert all(p["nullable"] for p in s["properties"].values())


def test_date_carries_a_format_hint():
    s = compile_schema({"when": "date"})
    node = s["properties"]["when"]
    assert node["type"] == "string"
    assert "YYYY-MM-DD" in node["description"]


def test_explicit_node_description_wins_over_hint():
    s = compile_schema({"when": {"type": "date", "description": "billing period end"}})
    assert s["properties"]["when"]["description"] == "billing period end"


def test_nested_object_and_array():
    s = compile_schema(
        {
            "vendor": {"name": "string", "tax_id": "string"},
            "items": {"type": "array", "items": {"sku": "string", "qty": "integer"}},
        }
    )
    assert s["properties"]["vendor"]["type"] == "object"
    assert sorted(s["properties"]["vendor"]["properties"]) == ["name", "tax_id"]
    items = s["properties"]["items"]
    assert items["type"] == "array"
    assert items["items"]["properties"]["qty"]["type"] == "integer"


def test_array_without_items_is_rejected():
    with pytest.raises(ValueError, match="items"):
        compile_schema({"xs": {"type": "array"}})


def test_unknown_type_falls_back_to_string():
    # An unrecognised scalar name should not crash a whole pipeline config.
    assert compile_schema({"x": "uuid"})["properties"]["x"]["type"] == "string"


def _doc() -> Document:
    d = Document(filename="inv.pdf", data=b"%PDF-fake", mime="application/pdf")
    d.stored_uri = "https://drive.google.com/file/d/abc"
    d.meta = {"sender": "billing@acme.test", "subject": "Invoice 42"}
    return d


def test_records_path_explodes_rows_and_carries_parent_fields(monkeypatch):
    ex = LLMExtractor(
        schema={"invoice_no": "string", "line_items": {"type": "array", "items": {"sku": "string"}}},
        records_path="line_items",
        carry_fields=["invoice_no"],
    )
    monkeypatch.setattr(
        ex, "_call", lambda doc: {"invoice_no": "INV-1", "line_items": [{"sku": "A"}, {"sku": "B"}]}
    )
    rows = ex.extract(_doc())
    assert [r["sku"] for r in rows] == ["A", "B"]
    assert all(r["invoice_no"] == "INV-1" for r in rows)
    # Provenance is attached to every row, not just the document.
    assert all(r["_source_file"] == "inv.pdf" for r in rows)
    assert all(r["_stored_uri"].endswith("abc") for r in rows)
    assert all(r["_sender"] == "billing@acme.test" for r in rows)


def test_single_record_without_records_path(monkeypatch):
    ex = LLMExtractor(schema={"invoice_no": "string"})
    monkeypatch.setattr(ex, "_call", lambda doc: {"invoice_no": "INV-9"})
    rows = ex.extract(_doc())
    assert len(rows) == 1 and rows[0]["invoice_no"] == "INV-9"


def test_records_path_pointing_at_a_non_list_is_rejected(monkeypatch):
    ex = LLMExtractor(schema={"x": "string"}, records_path="x")
    monkeypatch.setattr(ex, "_call", lambda doc: {"x": "not a list"})
    with pytest.raises(ValueError, match="not a list"):
        ex.extract(_doc())


def test_text_mode_prompt_includes_document_text(monkeypatch):
    ex = LLMExtractor(schema={"x": "string"}, max_text_chars=20)
    d = _doc()
    d.text = "A" * 100
    contents = ex._contents(d)
    assert len(contents) == 1 and isinstance(contents[0], str)
    # Truncated to max_text_chars, not sent whole.
    assert contents[0].count("A") == 20


def test_passthrough_mode_sends_raw_bytes():
    ex = LLMExtractor(schema={"x": "string"})
    contents = ex._contents(_doc())  # doc.text is None
    assert len(contents) == 2
    assert getattr(contents[0], "inline_data", None) is not None
