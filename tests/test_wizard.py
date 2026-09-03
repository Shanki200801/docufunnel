"""Wizard tests.

The prompting is deliberately thin and the logic lives in pure functions, so
these cover the parts that can silently produce a broken setup: env merging,
YAML generation, and the credential checks whose whole purpose is to fail
loudly at entry time rather than quietly at 3am.
"""

from __future__ import annotations

import imaplib
import json
import urllib.error
from pathlib import Path

import pytest
import yaml

from docufunnel import config
from docufunnel.introspect import json_schema
from docufunnel.wizard import (
    Field,
    build_pipeline_yaml,
    merge_env,
    suggest_dedupe_key,
    verify_gemini_key,
    verify_imap,
    write_files,
)

FIELDS = [
    Field("invoice_no", "string", "The invoice or receipt number"),
    Field("invoice_date", "date", "The date printed on the document"),
    Field("total", "number", 'Grand total, including "tax"'),
]


# -- env merging ------------------------------------------------------------


def test_merge_env_replaces_in_place_and_keeps_comments() -> None:
    existing = "# creds\nIMAP_USER=old@example.test\n\n# other\nKEEP=1\n"
    out = merge_env(existing, {"IMAP_USER": "new@example.test"})
    assert "IMAP_USER=new@example.test" in out
    assert "old@example.test" not in out
    # A hand-edited file must survive a re-run of the wizard.
    assert "# creds" in out and "# other" in out and "KEEP=1" in out
    assert out.index("IMAP_USER") < out.index("KEEP")


def test_merge_env_appends_new_keys() -> None:
    out = merge_env("A=1\n", {"B": "2", "C": "3"})
    assert out.splitlines()[0] == "A=1"
    assert "B=2" in out and "C=3" in out


def test_merge_env_from_empty() -> None:
    assert merge_env("", {"A": "1"}) == "A=1\n"


def test_merge_env_ignores_commented_out_assignments() -> None:
    out = merge_env("# A=old\n", {"A": "new"})
    # The comment is left alone and a real assignment is added.
    assert "# A=old" in out
    assert "A=new" in out


# -- yaml generation --------------------------------------------------------


def test_generated_yaml_parses_and_has_the_requested_fields() -> None:
    text = build_pipeline_yaml(name="t", source="imap", fields=FIELDS, sink="csv")
    parsed = yaml.safe_load(text)
    assert parsed["source"]["type"] == "imap"
    schema = parsed["extract"]["schema"]
    assert list(schema) == ["invoice_no", "invoice_date", "total"]
    # A description containing quotes must not break the YAML.
    assert schema["total"]["description"] == 'Grand total, including "tax"'


def test_generated_yaml_keeps_the_schema_header_for_editors() -> None:
    text = build_pipeline_yaml(name="t", source="local", fields=FIELDS, sink="csv")
    assert text.startswith("# yaml-language-server: $schema=")


def test_field_without_a_description_uses_the_shorthand() -> None:
    text = build_pipeline_yaml(
        name="t", source="local", fields=[Field("ref", "string", "")], sink="csv"
    )
    assert yaml.safe_load(text)["extract"]["schema"]["ref"] == "string"


@pytest.mark.parametrize("source", ["imap", "local"])
@pytest.mark.parametrize("sink", ["csv", "jsonl", "gsheet"])
def test_generated_yaml_validates_against_the_shipped_schema(source: str, sink: str) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    text = build_pipeline_yaml(name="t", source=source, fields=FIELDS, sink=sink)
    jsonschema.validate(yaml.safe_load(text), json_schema())


@pytest.mark.parametrize("source", ["imap", "local"])
def test_generated_yaml_is_a_loadable_config(source: str, tmp_path: Path, monkeypatch) -> None:
    """The wizard's output has to be runnable, not merely well-formed."""
    monkeypatch.setenv("IMAP_USER", "me@example.test")
    monkeypatch.setenv("IMAP_PASSWORD", "app-password")
    path = tmp_path / "p.yaml"
    path.write_text(build_pipeline_yaml(name="p", source=source, fields=FIELDS, sink="csv"))

    cfg = config.load(path)
    assert cfg.source.type == source
    assert cfg.extract is not None and cfg.sink is not None
    # Constructing the pipeline proves every adapter accepts these options.
    from docufunnel.pipeline import Pipeline

    Pipeline(cfg)


def test_dedupe_key_prefers_a_business_identifier() -> None:
    # A content hash cannot see that the same invoice was re-sent under a
    # different filename; an invoice number can.
    assert suggest_dedupe_key(FIELDS) == "invoice_no"
    assert suggest_dedupe_key([Field("vendor", "string", "")]) == "_uid"


def test_write_files_writes_pipeline_env_and_schema(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "pipelines" / "p.yaml"
    text = build_pipeline_yaml(name="p", source="local", fields=FIELDS, sink="csv")

    written = write_files(target, text, {"GEMINI_API_KEY": "k"}, json_schema())

    assert target.exists()
    assert (tmp_path / ".env").read_text().strip() == "GEMINI_API_KEY=k"
    schema_path = target.parent / ".docufunnel-schema.json"
    assert json.loads(schema_path.read_text())["title"] == "docufunnel pipeline"
    # Paths come back resolved, so a caller printing them gets a consistent
    # list rather than a mix of absolute and cwd-relative entries.
    assert set(written) == {
        target.resolve(),
        (tmp_path / ".env").resolve(),
        schema_path.resolve(),
    }


# -- credential checks ------------------------------------------------------


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_verify_gemini_key_accepts_a_200(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: _Resp())
    ok, message = verify_gemini_key("good-key")
    assert ok and message == "ok"


@pytest.mark.parametrize("code", [400, 401, 403])
def test_verify_gemini_key_explains_a_rejection(monkeypatch, code: int) -> None:
    def boom(url, timeout):
        raise urllib.error.HTTPError(url, code, "no", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    ok, message = verify_gemini_key("bad-key")
    assert not ok
    assert "rejected" in message


def test_verify_gemini_key_distinguishes_a_network_failure(monkeypatch) -> None:
    def boom(url, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    ok, message = verify_gemini_key("key")
    assert not ok
    # Not the user's key at fault, so the advice must differ.
    assert "connection" in message.lower()


def test_verify_gemini_key_does_not_leak_the_key_into_the_message(monkeypatch) -> None:
    def boom(url, timeout):
        raise urllib.error.HTTPError(url, 403, "no", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    _, message = verify_gemini_key("SECRET-KEY-VALUE")
    assert "SECRET-KEY-VALUE" not in message


def test_verify_imap_success(monkeypatch) -> None:
    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", lambda self, h, p: None)
    monkeypatch.setattr(imaplib.IMAP4_SSL, "login", lambda self, u, p: None)
    monkeypatch.setattr(imaplib.IMAP4_SSL, "logout", lambda self: None)
    ok, message = verify_imap("imap.gmail.com", "u", "p")
    assert ok and message == "ok"


def test_verify_imap_points_at_app_passwords(monkeypatch) -> None:
    def boom(self, u, p):
        raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", lambda self, h, p: None)
    monkeypatch.setattr(imaplib.IMAP4_SSL, "login", boom)
    ok, message = verify_imap("imap.gmail.com", "u", "p")
    assert not ok
    assert "app password" in message
    assert "myaccount.google.com/apppasswords" in message


def test_verify_imap_reports_an_unreachable_host(monkeypatch) -> None:
    def boom(self, h, p):
        raise OSError("no route to host")

    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", boom)
    ok, message = verify_imap("imap.invalid", "u", "p")
    assert not ok
    assert "Could not reach" in message
