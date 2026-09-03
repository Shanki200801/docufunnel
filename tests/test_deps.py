"""Missing-dependency errors.

Adapters import heavy dependencies lazily, which moves the failure to run
time. Left bare it reads `ModuleNotFoundError: No module named 'markitdown'` —
true, and useless to someone who does not know which extra provides it.
"""

from __future__ import annotations

import sys

import pytest

from docufunnel.core import Document
from docufunnel.deps import MissingDependency, missing


def test_message_names_the_extra_and_the_doctor_command() -> None:
    exc = missing("markitdown", "markitdown", "the default normalizer")
    text = str(exc)
    assert 'pip install "docufunnel[markitdown]"' in text
    assert "the default normalizer" in text
    assert "docufunnel doctor" in text


def test_docling_absence_is_reported_helpfully() -> None:
    """docling is genuinely not installed in the test environment, so this
    needs no mocking — it is the real failure a user would hit.
    """
    if "docling" in sys.modules:
        pytest.skip("docling is installed here, so there is no absence to test")

    from docufunnel.normalizers.docling_ import DoclingNormalizer

    with pytest.raises(MissingDependency, match=r"docufunnel\[docling\]"):
        DoclingNormalizer().to_text(Document(filename="a.pdf", data=b"%PDF"))


def _block(monkeypatch, *modules: str) -> None:
    """Make an installed module look absent. A None entry in sys.modules makes
    `import x` raise ImportError, which is what a real absence does.
    """
    for name in modules:
        monkeypatch.setitem(sys.modules, name, None)


def test_markitdown_absence_names_its_extra(monkeypatch) -> None:
    _block(monkeypatch, "markitdown")
    from docufunnel.normalizers.markitdown_ import MarkItDownNormalizer

    with pytest.raises(MissingDependency, match=r"docufunnel\[markitdown\]"):
        MarkItDownNormalizer().to_text(Document(filename="a.pdf", data=b"%PDF"))


def test_llm_extractor_absence_names_its_extra(monkeypatch) -> None:
    _block(monkeypatch, "google.genai")
    from docufunnel.extractors.llm import LLMExtractor

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with pytest.raises(MissingDependency, match=r"docufunnel\[llm\]"):
        LLMExtractor(schema={"x": "string"})._get_client()


def test_google_client_absence_names_its_extra(monkeypatch) -> None:
    from docufunnel import google_auth

    google_auth.credentials.cache_clear()
    google_auth.service.cache_clear()
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    _block(monkeypatch, "googleapiclient", "googleapiclient.discovery")

    with pytest.raises(MissingDependency, match=r"docufunnel\[google\]"):
        google_auth.service("sheets", "v4")

    google_auth.credentials.cache_clear()
    google_auth.service.cache_clear()


def test_a_missing_dependency_is_skipped_not_fatal(tmp_path, monkeypatch) -> None:
    """A pipeline must record the failure and move on, so one unusable adapter
    does not lose the whole run.
    """
    import yaml

    from docufunnel import config
    from docufunnel.pipeline import Pipeline

    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "a.pdf").write_bytes(b"%PDF-1.4 minimal")

    cfg_path = tmp_path / "p.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "name": "p",
        "source": {"type": "local", "path": str(tmp_path / "samples"), "glob": "*.pdf"},
        "normalize": {"type": "markitdown"},
        "extract": {"type": "regex", "fields": {"x": "(a)"}},
        "sink": {"type": "csv", "path": str(tmp_path / "out.csv")},
    }))

    _block(monkeypatch, "markitdown")
    result = Pipeline(config.load(cfg_path)).run()

    assert result.fetched == 1
    assert result.written == 0
    assert len(result.skipped) == 1
    assert "docufunnel[markitdown]" in result.skipped[0][1]
