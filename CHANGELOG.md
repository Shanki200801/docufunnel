# Changelog

## Unreleased

### Added
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
