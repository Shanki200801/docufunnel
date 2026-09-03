# Changelog

## Unreleased

### Added
- **Apps Script edition** (`apps-script/`): the same pipeline in a Google
  Sheet, with nothing to install. Runs as the signed-in user, so Gmail, Drive
  and Sheets need no credential setup at all — the only secret is a Gemini API
  key, checked on entry. Config lives in two spreadsheet tabs; the Fields tab's
  plain-English column becomes the extraction schema. Built-in hourly/daily
  triggers, a Log tab, a "Check my setup" report, and a test run that reads
  real mail while writing nothing.
- `docufunnel setup`: interactive setup for the Python edition. Verifies the
  Gemini key and IMAP app password at entry, asks what to extract in plain
  English, and writes `.env`, the pipeline and the editor schema.
- `docufunnel init`, `list --describe`, `doctor` and `schema`: adapter
  introspection turned into scaffolding, documentation, a JSON Schema for
  editor autocomplete, and a pre-flight check.
- `imap` source: read mail with a Gmail app password. No Google Cloud project,
  no OAuth consent screen, no expiring token, no extra dependency. Supports
  Gmail search syntax via `X-GM-RAW`, and marks processed mail with an IMAP
  keyword, a mailbox move, `\Seen`, or a local state file.
- Service-account credentials for Drive and Sheets, which Google's rules exempt
  from app verification. Share a Sheet with the account's email and that is the
  setup.
- `pipelines/imap-invoices.yaml`: the credential-light starting point.

### Changed
- Renamed from `docpipe` (taken on PyPI) to `docufunnel`.

## 0.1.0

Initial pipeline: `source -> store -> normalize -> extract -> sink`, with
adapters for Gmail, Drive, MarkItDown, Docling, Gemini, regex, Sheets, CSV and
JSONL; `${ENV}` interpolation, `{{var}}` path templating, and three
independent idempotency layers.
