"""Helpful failures for the optional dependencies.

Adapters import their heavy dependencies lazily, which keeps a minimal install
small but moves the failure to run time. Left alone, that surfaces as a bare
`ModuleNotFoundError: No module named 'markitdown'` — accurate, and useless to
someone who does not know which extra provides it.

`docufunnel doctor` reports this before a run, but a run that happens anyway
should say the same thing.
"""

from __future__ import annotations


class MissingDependency(RuntimeError):
    """An optional dependency an adapter needs is not installed."""


def missing(module: str, extra: str, used_for: str = "") -> MissingDependency:
    """Build the error to raise from a failed lazy import."""
    detail = f" ({used_for})" if used_for else ""
    return MissingDependency(
        f'{module} is not installed{detail}. Run:  pip install "docufunnel[{extra}]"'
        f"\nOr check everything at once with:  docufunnel doctor <your-config.yaml>"
    )
