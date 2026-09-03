"""Pipeline configuration: load YAML, interpolate ${ENV_VARS}, validate shape.

Only the outer skeleton is validated here. Each adapter reads its own keys out
of `options`, so adding a config field to an adapter never means editing this
file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class MissingEnvVar(RuntimeError):
    pass


def interpolate(value: Any) -> Any:
    """Recursively replace ${VAR} and ${VAR:-default} with environment values.

    Secrets therefore never live in the YAML, which can be committed.
    """
    if isinstance(value, str):

        def sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            env = os.environ.get(name)
            # An empty value counts as unset. GitHub Actions passes a secret
            # that was never configured as an empty string, and silently
            # interpolating that produces a confusing downstream API error
            # instead of naming the missing secret.
            if env:
                return env
            if default is not None:
                return default
            raise MissingEnvVar(
                f"${{{name}}} referenced in config but not set (or empty) in "
                f"the environment"
            )

        return _ENV_RE.sub(sub, value)
    if isinstance(value, dict):
        return {k: interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v) for v in value]
    return value


class StageConfig(BaseModel):
    """One slot's config. `type` picks the adapter, everything else is its own."""

    model_config = ConfigDict(extra="allow")

    type: str

    @property
    def options(self) -> dict[str, Any]:
        extra = self.model_extra or {}
        return {k: v for k, v in extra.items() if k != "fallback"}


class NormalizeConfig(StageConfig):
    """Normalizer with an optional fallback for when the primary yields nothing
    useful — the scanned-PDF case, where a text-layer parser returns near-empty
    output and an OCR-capable normalizer must take over.
    """

    fallback: FallbackConfig | None = None


class FallbackConfig(StageConfig):
    # Minimum characters the primary normalizer must produce to be trusted.
    min_text_len: int = 200


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # Stop after N documents. Useful for a first run against a big mailbox.
    limit: int | None = Field(default=None, ge=1)
    # Keep going when one document fails, instead of aborting the run.
    on_error: Literal["skip", "abort"] = "skip"
    # Skip every side effect: no store writes, no sink writes, no mark_done.
    dry_run: bool = False

    source: StageConfig
    store: StageConfig | None = None
    normalize: NormalizeConfig | None = None
    extract: StageConfig | None = None
    sink: StageConfig | None = None

    @model_validator(mode="after")
    def _require_sink_for_extract(self) -> PipelineConfig:
        if self.extract and not self.sink and not self.dry_run:
            raise ValueError(
                "extract is configured but sink is not; add a sink or set dry_run: true"
            )
        return self


NormalizeConfig.model_rebuild()


def load(path: str | Path) -> PipelineConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: expected a YAML mapping at the top level")
    raw.setdefault("name", Path(path).stem)
    return PipelineConfig.model_validate(interpolate(raw))
