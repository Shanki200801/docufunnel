"""docpipe — a config-driven document ETL pipeline.

    SOURCE -> STORE -> NORMALIZE -> EXTRACT -> SINK

Each slot is an adapter chosen by a `type:` string in YAML. Swapping Gmail for
a folder, or Sheets for Postgres, is a config edit rather than a code change.

Built-in adapters register themselves on import of the sub-packages below.
Heavy third-party imports (markitdown, docling, googleapiclient, google-genai)
are deliberately deferred into the methods that use them, so a pipeline that
only needs a folder and a CSV runs with no optional dependency installed.
"""

from __future__ import annotations

# Import for side effect: each module calls @register at import time.
from . import extractors, normalizers, sinks, sources, stores  # noqa: F401
from .config import PipelineConfig, load
from .core import Document, available, load_plugins, register, resolve
from .pipeline import Pipeline, RunResult

__version__ = "0.1.0"

__all__ = [
    "Document",
    "Pipeline",
    "PipelineConfig",
    "RunResult",
    "available",
    "load",
    "load_plugins",
    "register",
    "resolve",
    "run_file",
]


def run_file(path: str, **overrides) -> RunResult:
    """Convenience entry point: load a YAML pipeline and run it."""
    cfg = load(path)
    if overrides:
        cfg = cfg.model_copy(update=overrides)
    return Pipeline(cfg).run()
