"""CLI and introspection tests.

`list --describe`, `doctor`, `schema` and `init` are all derived from adapter
constructor signatures, so these tests are really about that derivation being
correct and staying correct as adapters change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from docufunnel.cli import main
from docufunnel.introspect import describe, json_schema

REPO = Path(__file__).resolve().parents[1]


# -- list / describe --------------------------------------------------------


def test_list_names_every_slot(capsys) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    for slot in ("source", "store", "normalize", "extract", "sink"):
        assert slot in out
    assert "imap" in out


def test_list_describe_shows_options_and_defaults(capsys) -> None:
    assert main(["list", "--describe", "--slot", "source"]) == 0
    out = capsys.readouterr().out
    assert "user: str  (required)" in out
    assert "host: str = 'imap.gmail.com'" in out
    # Only the requested slot.
    assert "gsheet" not in out


def test_describe_reads_the_module_docstring_when_the_class_has_none() -> None:
    # Adapters document themselves at module level, where there is room to
    # explain the trade-off.
    assert describe("source", "imap").summary.startswith("IMAP source")
    assert describe("normalize", "markitdown").summary.startswith("MarkItDown")


def test_describe_marks_missing_dependencies() -> None:
    # docling is intentionally not installed in the test environment.
    assert describe("normalize", "docling").requires == "docling"
    assert describe("source", "local").requires is None
    assert describe("source", "local").dependency_installed


def test_describe_handles_an_adapter_with_no_init() -> None:
    doc = describe("normalize", "passthrough")
    assert doc.options == []


# -- schema -----------------------------------------------------------------


def test_schema_has_a_branch_per_adapter() -> None:
    schema = json_schema()
    titles = {b["title"] for b in schema["properties"]["source"]["oneOf"]}
    assert {"source: imap", "source: gmail", "source: local"} <= titles


def test_schema_widens_scalars_to_accept_env_placeholders() -> None:
    """Every value may be written as "${VAR}", so a strict integer type would
    reject a valid config.
    """
    branches = json_schema()["properties"]["source"]["oneOf"]
    imap = next(b for b in branches if b["title"] == "source: imap")
    port = imap["properties"]["port"]
    assert "anyOf" in port
    assert {"type": "integer", "default": 993} in port["anyOf"]
    assert any(a.get("pattern") for a in port["anyOf"])


def test_schema_normalize_stage_is_recursive_for_fallback() -> None:
    schema = json_schema()
    assert schema["properties"]["normalize"] == {"$ref": "#/$defs/normalizeStage"}
    branch = schema["$defs"]["normalizeStage"]["oneOf"][0]
    assert branch["properties"]["fallback"] == {"$ref": "#/$defs/normalizeStage"}


@pytest.mark.parametrize("pipeline", sorted((REPO / "pipelines").glob("*.yaml")), ids=lambda p: p.name)
def test_every_shipped_pipeline_validates_against_the_schema(pipeline: Path) -> None:
    """The schema is only worth shipping if it accepts the configs we ship."""
    jsonschema = pytest.importorskip("jsonschema")
    raw = yaml.safe_load(pipeline.read_text())
    jsonschema.validate(raw, json_schema())


def test_schema_command_writes_a_file(tmp_path, capsys) -> None:
    out = tmp_path / "s.json"
    assert main(["schema", "-o", str(out)]) == 0
    schema = json.loads(out.read_text())
    assert schema["title"] == "docufunnel pipeline"
    assert "yaml-language-server" in capsys.readouterr().out


# -- init -------------------------------------------------------------------


def test_init_generates_a_config_that_validates_and_runs(tmp_path, monkeypatch, capsys) -> None:
    """The end-to-end contract of `init`: what it writes must actually work."""
    from conftest import INVOICE_TEXT, _write_pdf

    monkeypatch.chdir(tmp_path)
    (tmp_path / "samples").mkdir()
    _write_pdf(tmp_path / "samples" / "invoice.pdf", INVOICE_TEXT)

    cfg = tmp_path / "p.yaml"
    assert main([
        "init", "--non-interactive",
        "--source", "local", "--extract", "regex", "--sink", "csv",
        "-o", str(cfg),
    ]) == 0

    # A YAML syntax error here would mean the generated hints are unparseable —
    # double-quoting a regex containing \s did exactly that.
    assert yaml.safe_load(cfg.read_text())
    assert main(["validate", str(cfg)]) == 0
    assert main(["run", str(cfg)]) == 0

    rows = (tmp_path / "out" / "records.csv").read_text().splitlines()
    assert "INV-2026-0042" in rows[1]


def test_init_emits_the_schema_header_and_sidecar(tmp_path) -> None:
    cfg = tmp_path / "p.yaml"
    main(["init", "--non-interactive", "-o", str(cfg)])
    assert cfg.read_text().startswith("# yaml-language-server: $schema=")
    assert (tmp_path / ".docufunnel-schema.json").exists()


def test_init_leaves_a_todo_for_an_uncovered_required_option(tmp_path) -> None:
    cfg = tmp_path / "p.yaml"
    main(["init", "--non-interactive", "--sink", "gsheet", "-o", str(cfg)])
    text = cfg.read_text()
    # gsheet's spreadsheet_id is covered by a hint, so no TODO for it.
    assert "spreadsheet_id: ${SHEET_ID}" in text
    assert "# TODO: spreadsheet_id" not in text


def test_init_refuses_to_clobber(tmp_path, capsys) -> None:
    cfg = tmp_path / "p.yaml"
    cfg.write_text("name: existing\n")
    assert main(["init", "--non-interactive", "-o", str(cfg)]) == 2
    assert cfg.read_text() == "name: existing\n"
    assert main(["init", "--non-interactive", "--force", "-o", str(cfg)]) == 0
    assert "existing" not in cfg.read_text()


def test_init_rejects_an_unknown_adapter(tmp_path, capsys) -> None:
    assert main([
        "init", "--non-interactive", "--source", "nope", "-o", str(tmp_path / "p.yaml")
    ]) == 2
    assert "unknown source" in capsys.readouterr().err


# -- doctor / errors --------------------------------------------------------


def test_doctor_without_a_config_reports_environment(capsys) -> None:
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "optional dependencies" in out
    assert "google credentials" in out


def test_doctor_flags_a_missing_variable(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("SHEET_ID", raising=False)
    cfg = tmp_path / "p.yaml"
    cfg.write_text(
        "name: p\nsource:\n  type: local\n  path: .\nsink:\n"
        "  type: csv\n  path: ${SHEET_ID}\n"
    )
    assert main(["doctor", str(cfg)]) == 1
    assert "SHEET_ID" in capsys.readouterr().out


def test_doctor_on_a_missing_file(tmp_path, capsys) -> None:
    assert main(["doctor", str(tmp_path / "nope.yaml")]) == 2


def test_invalid_yaml_reports_a_location_not_a_traceback(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nsource:\n  type: local\n   bad: indent\n")
    assert main(["validate", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "invalid YAML" in err and "line 4" in err


def test_run_on_a_missing_file_is_a_message(tmp_path, capsys) -> None:
    assert main(["run", str(tmp_path / "nope.yaml")]) == 2
    assert "no such config file" in capsys.readouterr().err
