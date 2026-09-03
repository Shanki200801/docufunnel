"""The orchestrator. Deliberately dumb: it knows the order of the slots and
nothing about any particular adapter.

Ordering guarantee that matters: a document is only marked done on the source
after its records have reached the sink. A crash mid-run therefore re-processes
the tail rather than losing it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from .config import NormalizeConfig, PipelineConfig, StageConfig
from .core import Document, load_plugins, resolve

log = logging.getLogger("docpipe")


@dataclass
class RunResult:
    pipeline: str
    fetched: int = 0
    stored: int = 0
    normalized: int = 0
    extracted_records: int = 0
    written: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"pipeline={self.pipeline}",
            f"fetched={self.fetched}",
            f"stored={self.stored}",
            f"normalized={self.normalized}",
            f"records={self.extracted_records}",
            f"written={self.written}",
            f"skipped={len(self.skipped)}",
        ]
        out = "  ".join(lines)
        for name, why in self.skipped:
            out += f"\n  ! {name}: {why}"
        return out


def _build(slot: str, cfg: StageConfig | None) -> Any:
    if cfg is None:
        return None
    cls = resolve(slot, cfg.type)
    return cls(**cfg.options)


class Pipeline:
    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        load_plugins()
        self.source = _build("source", cfg.source)
        self.store = _build("store", cfg.store)
        self.normalizer = _build("normalize", cfg.normalize)
        self.fallback = None
        self.fallback_threshold = 0
        if isinstance(cfg.normalize, NormalizeConfig) and cfg.normalize.fallback:
            self.fallback = _build("normalize", cfg.normalize.fallback)
            self.fallback_threshold = cfg.normalize.fallback.min_text_len
        self.extractor = _build("extract", cfg.extract)
        self.sink = _build("sink", cfg.sink)

    def run(self) -> RunResult:
        cfg = self.cfg
        res = RunResult(pipeline=cfg.name)
        batch: list[Document] = []
        # Sheets/CSV appends are far cheaper in bulk, but flushing periodically
        # bounds how much work a crash can throw away.
        batch_size = 50

        docs = self.source.fetch()
        if cfg.limit:
            docs = islice(docs, cfg.limit)

        for doc in docs:
            res.fetched += 1
            try:
                self._process(doc, res)
            except Exception as exc:
                log.warning("failed %s: %s", doc.filename, exc)
                res.skipped.append((doc.filename, str(exc)))
                if cfg.on_error == "abort":
                    self._flush(batch, res)
                    raise
                continue

            batch.append(doc)
            if len(batch) >= batch_size:
                self._flush(batch, res)
                batch = []

        self._flush(batch, res)
        if self.sink and not cfg.dry_run:
            self.sink.close()
        return res

    def _process(self, doc: Document, res: RunResult) -> None:
        if self.store and not self.cfg.dry_run:
            doc.stored_uri = self.store.put(doc)
            res.stored += 1

        if self.normalizer:
            doc.text = self.normalizer.to_text(doc)
            # A text-layer parser on a scanned PDF succeeds and returns almost
            # nothing, so length is the signal, not an exception.
            if self.fallback and len(doc.text or "") < self.fallback_threshold:
                log.info(
                    "%s: primary normalizer gave %d chars, falling back to %s",
                    doc.filename,
                    len(doc.text or ""),
                    type(self.fallback).adapter_name,
                )
                doc.text = self.fallback.to_text(doc)
            if doc.text:
                res.normalized += 1

        if self.extractor:
            doc.records = self.extractor.extract(doc)
            res.extracted_records += len(doc.records)

    def _flush(self, batch: list[Document], res: RunResult) -> None:
        if not batch:
            return
        if self.sink and not self.cfg.dry_run:
            res.written += self.sink.write(batch)
        if not self.cfg.dry_run:
            for doc in batch:
                self.source.mark_done(doc)
