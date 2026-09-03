"""CSV / JSONL sinks. No credentials, so these are what you point a new
pipeline at while you are still shaping the schema.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..core import Document, register


def _flatten(value: Any) -> Any:
    """CSV cells are scalars. Nested output from the model is JSON-encoded
    rather than dropped, so nothing is silently lost.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else value


@register("sink", "csv")
class CsvSink:
    def __init__(self, path: str = "out/records.csv", dedupe_key: str | None = None) -> None:
        self.path = Path(path).expanduser()
        self.dedupe_key = dedupe_key
        self._seen: set[str] | None = None
        self._header: list[str] | None = None

    def _load_existing(self) -> None:
        if self._seen is not None:
            return
        self._seen = set()
        if not self.path.exists():
            return
        with self.path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            self._header = reader.fieldnames and list(reader.fieldnames) or None
            if self.dedupe_key:
                for row in reader:
                    val = row.get(self.dedupe_key)
                    if val:
                        self._seen.add(str(val))

    def write(self, docs: list[Document]) -> int:
        self._load_existing()
        assert self._seen is not None

        rows: list[dict[str, Any]] = []
        for doc in docs:
            for rec in doc.records:
                if self.dedupe_key:
                    key = str(rec.get(self.dedupe_key) or "")
                    if key and key in self._seen:
                        continue
                    if key:
                        self._seen.add(key)
                rows.append(rec)
        if not rows:
            return 0

        # Header is the union of keys, preserving first-seen order, and never
        # reorders columns that already exist in the file.
        header = list(self._header or [])
        for rec in rows:
            for k in rec:
                if k not in header:
                    header.append(k)

        rewrite = header != (self._header or [])
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if rewrite and self.path.exists():
            # A new column appeared: re-emit the file under the wider header.
            with self.path.open(newline="") as fh:
                old = list(csv.DictReader(fh))
            with self.path.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
                w.writeheader()
                for r in old:
                    w.writerow({k: _flatten(r.get(k, "")) for k in header})
                for r in rows:
                    w.writerow({k: _flatten(r.get(k)) for k in header})
        else:
            new_file = not self.path.exists()
            with self.path.open("a", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
                if new_file:
                    w.writeheader()
                for r in rows:
                    w.writerow({k: _flatten(r.get(k)) for k in header})

        self._header = header
        return len(rows)

    def close(self) -> None:
        pass


@register("sink", "jsonl")
class JsonlSink:
    """Lossless counterpart to the CSV sink — keeps nested structures intact."""

    def __init__(self, path: str = "out/records.jsonl", dedupe_key: str | None = None) -> None:
        self.path = Path(path).expanduser()
        self.dedupe_key = dedupe_key
        self._seen: set[str] | None = None

    def _load_existing(self) -> None:
        if self._seen is not None:
            return
        self._seen = set()
        if self.dedupe_key and self.path.exists():
            for line in self.path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    val = json.loads(line).get(self.dedupe_key)
                except json.JSONDecodeError:
                    continue
                if val:
                    self._seen.add(str(val))

    def write(self, docs: list[Document]) -> int:
        self._load_existing()
        assert self._seen is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self.path.open("a") as fh:
            for doc in docs:
                for rec in doc.records:
                    if self.dedupe_key:
                        key = str(rec.get(self.dedupe_key) or "")
                        if key and key in self._seen:
                            continue
                        if key:
                            self._seen.add(key)
                    fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                    written += 1
        return written

    def close(self) -> None:
        pass
