"""LLM extractor — config-declared schema, structured JSON out.

This is the slot that makes the tool general. A regex template has to be
written per vendor layout and breaks when the layout shifts; a schema
describes what you want, not where it sits on the page, so one config handles
arbitrary senders.

Two input modes:
  * doc.text set (a normalizer ran)  -> cheap, text-only prompt
  * doc.text None (passthrough)      -> raw file sent inline, layout preserved

Gemini is the default provider because its free tier is the only one generous
enough to run this on a mailbox without a bill.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from ..core import Document, register

log = logging.getLogger("docufunnel.extract")

# YAML shorthand -> JSON-schema type. `date` and `currency` are not real JSON
# schema types; they map to string and carry a format hint in the description
# so the model returns a normalised value.
_SCALARS = {
    "string": ("string", None),
    "str": ("string", None),
    "text": ("string", None),
    "number": ("number", None),
    "float": ("number", None),
    "integer": ("integer", None),
    "int": ("integer", None),
    "boolean": ("boolean", None),
    "bool": ("boolean", None),
    "date": ("string", "ISO 8601 date, YYYY-MM-DD"),
    "datetime": ("string", "ISO 8601 timestamp"),
    "currency": ("string", "ISO 4217 currency code, e.g. USD"),
}

DEFAULT_PROMPT = (
    "Extract the requested fields from this document. "
    "Use null for any field that is genuinely absent — never invent a value. "
    "Return numbers as numbers, not strings, and strip currency symbols and "
    "thousands separators."
)


def compile_schema(spec: Any) -> dict[str, Any]:
    """Turn the YAML `schema:` block into a JSON schema the model can be
    constrained to.

    Accepts shorthand (`total: number`), explicit form
    (`total: {type: number, description: ...}`), nested objects, and arrays
    (`items: {...}`).
    """
    if isinstance(spec, str):
        json_type, hint = _SCALARS.get(spec.lower(), ("string", None))
        node: dict[str, Any] = {"type": json_type, "nullable": True}
        if hint:
            node["description"] = hint
        return node

    if not isinstance(spec, dict):
        raise TypeError(f"cannot compile schema fragment: {spec!r}")

    # Explicit node: has a recognised `type` key.
    declared = spec.get("type")
    if isinstance(declared, str):
        low = declared.lower()
        if low in ("array", "list"):
            items = spec.get("items")
            if items is None:
                raise ValueError("array schema requires `items`")
            return {"type": "array", "items": compile_schema(items)}
        if low in ("object", "dict"):
            props = spec.get("properties") or {
                k: v for k, v in spec.items() if k not in ("type", "description")
            }
            return compile_schema(props)
        json_type, hint = _SCALARS.get(low, ("string", None))
        node = {"type": json_type, "nullable": True}
        desc = spec.get("description") or hint
        if desc:
            node["description"] = desc
        return node

    # Implicit object: a mapping of field name -> schema fragment.
    return {
        "type": "object",
        "properties": {k: compile_schema(v) for k, v in spec.items()},
        # Every key is required so the model must emit it, and nullable lets it
        # say "not present" without dropping the field.
        "required": list(spec.keys()),
    }


@register("extract", "llm")
class LLMExtractor:
    def __init__(
        self,
        schema: Any,
        model: str = "gemini-2.5-flash",
        prompt: str = DEFAULT_PROMPT,
        api_key_env: str = "GEMINI_API_KEY",
        # When one document contains many rows (invoice line items, statement
        # transactions), name the array field to explode into separate records.
        records_path: str | None = None,
        # Fields copied onto every exploded record from the document level.
        carry_fields: list[str] | None = None,
        max_text_chars: int = 60_000,
        retries: int = 3,
        temperature: float = 0.0,
    ) -> None:
        self.schema_spec = schema
        self.schema = compile_schema(schema)
        self.model = model
        self.prompt = prompt
        self.api_key_env = api_key_env
        self.records_path = records_path
        self.carry_fields = carry_fields or []
        self.max_text_chars = max_text_chars
        self.retries = retries
        self.temperature = temperature
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        from google import genai

        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"{self.api_key_env} not set. Get a free key at "
                "https://aistudio.google.com/apikey"
            )
        self._client = genai.Client(api_key=key)
        return self._client

    def _contents(self, doc: Document) -> list[Any]:
        from google.genai import types

        if doc.text:
            body = doc.text[: self.max_text_chars]
            if len(doc.text) > self.max_text_chars:
                log.warning(
                    "%s: text truncated to %d chars", doc.filename, self.max_text_chars
                )
            return [f"{self.prompt}\n\n--- DOCUMENT: {doc.filename} ---\n{body}"]

        return [
            types.Part.from_bytes(data=doc.data, mime_type=doc.mime),
            self.prompt,
        ]

    def _call(self, doc: Document) -> dict[str, Any]:
        from google.genai import types

        client = self._get_client()
        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=self.schema,
            temperature=self.temperature,
        )
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = client.models.generate_content(
                    model=self.model, contents=self._contents(doc), config=cfg
                )
                return json.loads(resp.text)
            except Exception as exc:  # noqa: BLE001 - provider errors vary
                last = exc
                # Free-tier rate limits are the common failure; back off rather
                # than losing the document.
                wait = 2**attempt
                log.warning(
                    "%s: extract attempt %d/%d failed (%s), retrying in %ds",
                    doc.filename,
                    attempt + 1,
                    self.retries,
                    exc,
                    wait,
                )
                if attempt < self.retries - 1:
                    time.sleep(wait)
        raise RuntimeError(f"extraction failed after {self.retries} attempts: {last}")

    def extract(self, doc: Document) -> list[dict[str, Any]]:
        payload = self._call(doc)
        provenance = {
            "_source_file": doc.filename,
            "_stored_uri": doc.stored_uri or "",
            "_uid": doc.uid,
            "_sender": doc.meta.get("sender", ""),
            "_subject": doc.meta.get("subject", ""),
        }

        if not self.records_path:
            return [{**payload, **provenance}]

        rows = payload.get(self.records_path) or []
        if not isinstance(rows, list):
            raise ValueError(  # noqa: TRY004 - bad model output is a value, not a type, error
                f"records_path {self.records_path!r} is not a list in the model output"
            )
        carried = {k: payload.get(k) for k in self.carry_fields}
        return [{**carried, **row, **provenance} for row in rows if isinstance(row, dict)]
