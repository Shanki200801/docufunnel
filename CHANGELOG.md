# Changelog

## 0.1.0 — 2026-09-04

First release. Two editions of the same five-stage pipeline
(`source -> store -> normalize -> extract -> sink`), each stage an adapter
chosen by a `type:` string.

### Python edition

- **Adapters**: `imap` / `gmail` / `local` sources, `gdrive` / `local` stores,
  `markitdown` / `docling` / `passthrough` normalizers, `llm` (Gemini) /
  `regex` / `text` extractors, `gsheet` / `csv` / `jsonl` sinks. Optional
  dependencies are imported lazily, so a folder-to-CSV pipeline runs with no
  extras installed.
- **`imap` source**: read mail with a Gmail app password — no Google Cloud
  project, no OAuth consent screen, no expiring token, and no extra dependency
  (standard library only). Gmail's own search syntax works via `X-GM-RAW`, and
  processed mail is excluded server-side so it is never re-downloaded.
- **Service-account credentials** for Drive and Sheets, which Google's rules
  exempt from app verification: share a Sheet with the account's email and that
  is the setup.
- **`docufunnel setup`**: guided setup that verifies each credential as it is
  entered — the Gemini key against `models.list`, the app password by logging
  in to IMAP — asks what to extract in plain English, and writes `.env`, the
  pipeline and the editor schema.
- **Self-documenting config**: `list --describe`, `doctor`, `schema` and `init`
  are all derived from adapter constructor signatures, so there is no second
  copy to drift. `schema` emits a JSON Schema giving per-adapter completion and
  inline validation in any editor with the YAML language server.
- **Configuration**: `${ENV_VAR}` interpolation (an empty value counts as
  unset, which is how an unconfigured CI secret arrives), `{{var}}` templating
  for Drive paths and sheet tabs, and a normalize `fallback` that triggers on
  output length — a scanned PDF converts to near-empty text without raising, so
  length is the only usable signal.
- **Idempotency**, three independent layers: a source is marked done only
  after its rows reach the sink, sinks drop rows whose `dedupe_key` already
  exists, and the Drive store skips a filename already present.
- **Extensible**: one class plus a `@register` decorator adds an adapter;
  third-party packages can ship adapters through the `docufunnel.adapters`
  entry point group.
- Runs free on GitHub Actions cron; the repo doubles as a template.

### Apps Script edition

The same pipeline in a Google Sheet, with nothing to install. It runs as the
signed-in user, so Gmail, Drive and Sheets need no credential setup at all —
the only secret is a Gemini API key, checked on entry. Config lives in two
spreadsheet tabs, where the Fields tab's plain-English column becomes the
extraction schema. Includes built-in hourly and daily triggers, a Log tab, a
"Check my setup" report, and a test run that reads real mail while writing
nothing.

No normalize stage, deliberately: Gemini reads a PDF directly, so layout
survives and scanned pages work without OCR.
