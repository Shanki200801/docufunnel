"""Core contracts: the Document that flows through the pipeline, the adapter
protocols each slot must satisfy, and the registry that resolves a config
`type:` string to an adapter class.

Adding an adapter means writing one class and decorating it with @register.
Third-party packages can add adapters without touching this repo by exposing a
`docpipe.adapters` entry point (see load_plugins).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable

Slot = str  # "source" | "store" | "normalize" | "extract" | "sink"

SLOTS: tuple[Slot, ...] = ("source", "store", "normalize", "extract", "sink")


@dataclass
class Document:
    """One unit of work moving through the pipeline.

    Each stage enriches it in place rather than returning a new type, so a
    sink can still see where the file came from (sender, subject, thread).
    """

    filename: str
    data: bytes
    mime: str = "application/octet-stream"
    # Stable identity used for dedupe. Defaults to a content hash so the same
    # attachment arriving twice is recognised even across sources.
    uid: str = ""
    # Whatever the source knows: sender, subject, received_at, thread_id, ...
    meta: dict[str, Any] = field(default_factory=dict)
    # Set by the store slot once the bytes are persisted somewhere durable.
    stored_uri: str | None = None
    # Set by the normalize slot. None means "extractor gets raw bytes".
    text: str | None = None
    # Set by the extract slot. One document may yield several rows.
    records: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.uid:
            self.uid = hashlib.sha256(self.data).hexdigest()[:16]

    @property
    def size(self) -> int:
        return len(self.data)


@runtime_checkable
class Source(Protocol):
    """Yields documents to process. Lazy: a run may stop early on error."""

    def fetch(self) -> Iterator[Document]: ...

    def mark_done(self, doc: Document) -> None:
        """Called after a document completes every later stage successfully.

        Source-native bookkeeping (a Gmail label, a moved file) lives here so
        a re-run skips what already landed. No-op is a valid implementation.
        """


@runtime_checkable
class Store(Protocol):
    """Persists raw bytes and records the location on the document."""

    def put(self, doc: Document) -> str: ...


@runtime_checkable
class Normalizer(Protocol):
    """Turns arbitrary bytes into text (markdown) for the extractor."""

    def to_text(self, doc: Document) -> str | None: ...


@runtime_checkable
class Extractor(Protocol):
    """Turns text (or raw bytes) into zero or more structured records."""

    def extract(self, doc: Document) -> list[dict[str, Any]]: ...


@runtime_checkable
class Sink(Protocol):
    """Writes records out. Responsible for its own dedupe if configured."""

    def write(self, docs: list[Document]) -> int: ...

    def close(self) -> None:
        """Flush batched writes. No-op is valid."""


_REGISTRY: dict[tuple[Slot, str], type] = {}


def register(slot: Slot, name: str):
    """Class decorator that makes an adapter addressable from YAML."""
    if slot not in SLOTS:
        raise ValueError(f"unknown slot {slot!r}, expected one of {SLOTS}")

    def deco(cls: type) -> type:
        key = (slot, name)
        if key in _REGISTRY and _REGISTRY[key] is not cls:
            raise ValueError(f"{slot}.{name} already registered by {_REGISTRY[key]}")
        _REGISTRY[key] = cls
        cls.adapter_name = name  # type: ignore[attr-defined]
        cls.adapter_slot = slot  # type: ignore[attr-defined]
        return cls

    return deco


def resolve(slot: Slot, name: str) -> type:
    try:
        return _REGISTRY[(slot, name)]
    except KeyError:
        known = sorted(n for s, n in _REGISTRY if s == slot)
        raise KeyError(
            f"no {slot} adapter named {name!r}. available: {', '.join(known) or '(none)'}"
        ) from None


def available(slot: Slot | None = None) -> dict[Slot, list[str]]:
    out: dict[Slot, list[str]] = {s: [] for s in SLOTS}
    for s, n in _REGISTRY:
        out[s].append(n)
    for names in out.values():
        names.sort()
    return {slot: out[slot]} if slot else out


def load_plugins() -> list[str]:
    """Import external adapter packages advertising a `docpipe.adapters` entry
    point. Each entry point is a module whose import side effect is calling
    @register. Failures are reported, not fatal — one broken plugin should not
    stop a pipeline that does not use it.
    """
    loaded: list[str] = []
    for ep in entry_points(group="docpipe.adapters"):
        try:
            ep.load()
            loaded.append(ep.name)
        except Exception as exc:  # noqa: BLE001 - plugin isolation is the point
            print(f"[docpipe] plugin {ep.name!r} failed to load: {exc}")
    return loaded
