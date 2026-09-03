"""Adapter introspection: turn constructor signatures into documentation.

An adapter's keyword arguments *are* its config schema, so the signature is
the single source of truth. Deriving `list --describe`, `doctor` and the JSON
Schema from it means there is no second copy to drift out of date.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import typing
from dataclasses import dataclass
from typing import Any

from .core import SLOTS, Slot, available, resolve

# Optional dependency -> the extra that installs it, for doctor's advice.
OPTIONAL_DEPS: dict[str, str] = {
    "markitdown": "markitdown",
    "docling": "docling",
    "google.genai": "llm",
    "googleapiclient": "google",
    "google_auth_oauthlib": "google",
}

# Which import each adapter needs at call time. Adapters import lazily, so a
# missing dependency only surfaces when the adapter actually runs — doctor
# exists to surface it earlier.
ADAPTER_DEPS: dict[tuple[str, str], str] = {
    ("source", "gmail"): "googleapiclient",
    ("store", "gdrive"): "googleapiclient",
    ("sink", "gsheet"): "googleapiclient",
    ("normalize", "markitdown"): "markitdown",
    ("normalize", "docling"): "docling",
    ("extract", "llm"): "google.genai",
}

_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


@dataclass
class Option:
    name: str
    type_name: str
    json_type: list[str]
    required: bool
    default: Any

    def render(self) -> str:
        bits = f"{self.name}: {self.type_name}"
        if self.required:
            return f"{bits}  (required)"
        return f"{bits} = {self.default!r}"


@dataclass
class AdapterDoc:
    slot: Slot
    name: str
    summary: str
    options: list[Option]
    requires: str | None

    @property
    def dependency_installed(self) -> bool:
        return self.requires is None or module_available(self.requires)


def module_available(dotted: str) -> bool:
    """True if a module can be imported without importing it.

    find_spec on a submodule imports the parent package, which is cheap for
    the namespace packages here and avoids pulling in docling's models.
    """
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _hints(cls: type) -> dict[str, Any]:
    """Resolve annotations, which are strings under `from __future__ import
    annotations`. Falls back to the raw strings if a name cannot be resolved.
    """
    init = cls.__dict__.get("__init__")
    if init is None:
        return {}
    try:
        return typing.get_type_hints(init)
    except Exception:  # noqa: BLE001 - a partially resolvable module is fine
        return getattr(init, "__annotations__", {})


def _json_types(annotation: Any) -> tuple[str, list[str]]:
    """Map a Python annotation to a readable name plus JSON Schema types."""
    if annotation is inspect.Parameter.empty:
        return "any", []

    if isinstance(annotation, str):
        # Unresolvable annotation: show it verbatim, claim no JSON type.
        return annotation, []

    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "types.UnionType":
        parts, names = [], []
        for arg in typing.get_args(annotation):
            if arg is type(None):
                parts.append("null")
                names.append("None")
                continue
            name, types_ = _json_types(arg)
            names.append(name)
            parts.extend(types_)
        # dict.fromkeys dedupes while keeping order stable for readable output.
        return " | ".join(names), list(dict.fromkeys(parts))

    if origin in (list, dict, set, tuple):
        return _describe_name(annotation), [_JSON_TYPES.get(origin, "object")]

    if annotation in _JSON_TYPES:
        return annotation.__name__, [_JSON_TYPES[annotation]]

    if annotation is Any:
        return "any", []

    return _describe_name(annotation), []


def _describe_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", None) or str(annotation).replace("typing.", "")


def describe(slot: Slot, name: str) -> AdapterDoc:
    cls = resolve(slot, name)
    hints = _hints(cls)
    options: list[Option] = []

    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError):
        sig = None

    for param in (sig.parameters.values() if sig else ()):
        if param.name == "self" or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = hints.get(param.name, param.annotation)
        type_name, json_types = _json_types(annotation)
        options.append(
            Option(
                name=param.name,
                type_name=type_name,
                json_type=json_types,
                required=param.default is inspect.Parameter.empty,
                default=None if param.default is inspect.Parameter.empty else param.default,
            )
        )

    summary = _summary(cls)
    return AdapterDoc(
        slot=slot,
        name=name,
        summary=summary,
        options=options,
        requires=ADAPTER_DEPS.get((slot, name)),
    )


def _summary(cls: type) -> str:
    """First paragraph of the class docstring, or of its module's.

    Most adapters document themselves at module level, where there is room to
    explain the trade-off, so falling back there gives a far better one-liner
    than an empty string.
    """
    doc = (cls.__doc__ or "").strip()
    if not doc:
        module = sys.modules.get(cls.__module__)
        doc = (getattr(module, "__doc__", "") or "").strip()
    if not doc:
        return ""
    return doc.split("\n\n")[0].replace("\n", " ").strip()


def describe_all() -> dict[Slot, list[AdapterDoc]]:
    return {
        slot: [describe(slot, name) for name in names]
        for slot, names in available().items()
    }


def _stage_schema(docs: list[AdapterDoc], slot: Slot) -> dict[str, Any]:
    """One branch per adapter, discriminated by `type`.

    Editors key off the const to offer only that adapter's options, which is
    what turns the YAML into something autocompleting rather than guesswork.
    """
    branches = []
    for doc in docs:
        props: dict[str, Any] = {"type": {"const": doc.name}}
        required = ["type"]
        for opt in doc.options:
            node: dict[str, Any] = {}
            if opt.json_type:
                node["type"] = opt.json_type[0] if len(opt.json_type) == 1 else opt.json_type
            if not opt.required:
                node["default"] = opt.default
            # Every value may be written as "${ENV_VAR}", so a strict scalar
            # type would flag valid configs. Widen non-string scalars.
            if node.get("type") in ("integer", "number", "boolean"):
                node = {"anyOf": [node, {"type": "string", "pattern": r"^\$\{.+\}$"}]}
            props[opt.name] = node
            if opt.required:
                required.append(opt.name)
        branch: dict[str, Any] = {
            "title": f"{slot}: {doc.name}",
            "description": doc.summary,
            "properties": props,
            "required": required,
        }
        if slot == "normalize":
            branch["properties"]["fallback"] = {"$ref": "#/$defs/normalizeStage"}
        branches.append(branch)
    return {"type": "object", "oneOf": branches}


def json_schema() -> dict[str, Any]:
    """A JSON Schema for a pipeline file.

    Referenced from a config's first line as
    `# yaml-language-server: $schema=...`, this gives completion, hover docs
    and inline validation in any editor with the YAML language server.
    """
    docs = describe_all()
    normalize_stage = _stage_schema(docs["normalize"], "normalize")
    normalize_stage.setdefault("properties", {})
    normalize_stage["properties"]["min_text_len"] = {
        "type": "integer",
        "default": 200,
        "description": (
            "Fallback only: characters the primary normalizer must produce to "
            "be trusted. A scanned PDF converts to near-empty text without "
            "raising, so length is the signal."
        ),
    }

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "docufunnel pipeline",
        "type": "object",
        "additionalProperties": False,
        "required": ["source"],
        "$defs": {"normalizeStage": normalize_stage},
        "properties": {
            "name": {"type": "string", "description": "Pipeline name; defaults to the filename."},
            "limit": {
                "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
                "description": "Stop after N documents.",
            },
            "on_error": {
                "enum": ["skip", "abort"],
                "default": "skip",
                "description": "skip keeps going past a failed document; abort stops the run.",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "No store writes, no sink writes, no source marking.",
            },
        },
    }
    for slot in SLOTS:
        schema["properties"][slot] = (
            {"$ref": "#/$defs/normalizeStage"}
            if slot == "normalize"
            else _stage_schema(docs[slot], slot)
        )
    return schema
