# docpipe

Get structured data out of documents that arrive as attachments, without writing a new script per use case.

```
SOURCE ──▶ STORE ──▶ NORMALIZE ──▶ EXTRACT ──▶ SINK
 Gmail     Drive      MarkItDown     Gemini      Sheets
 folder    folder     Docling        regex       CSV / JSONL
                      passthrough    text
```

Every slot is an adapter picked by a `type:` string in YAML. Swapping Gmail for a watched folder, or Sheets for a CSV, is a config edit — not a code change. Runs free on GitHub Actions cron.

## Why the slots are split this way

The thing most tools get wrong is collapsing *normalize* and *extract* into one step. They are different problems:

- **Normalize** turns arbitrary bytes into text. [MarkItDown](https://github.com/microsoft/markitdown) does this for PDF, DOCX, XLSX, PPTX, HTML, CSV, EPub, images and ZIP — so the pipeline is not PDF-only. A vendor who mails a spreadsheet still works.
- **Extract** turns text into rows against a schema you declared. That is a different tool (a model, or a regex table).

Keeping them separate is what makes both replaceable.

### The scanned-PDF trap

MarkItDown's PDF path is `pdfminer.six`: no OCR. A scanned PDF converts *successfully* to near-empty text — it does not raise. So the normalize slot takes a `fallback` keyed on **output length**, not on exceptions:

```yaml
normalize:
  type: markitdown
  fallback:
    type: docling        # real layout models + OCR
    min_text_len: 200
```

Docling is slower and downloads models on first use, which is why it is the fallback and not the default.

### Text vs. raw PDF into the model

| Input mode | Cost | Layout preserved | Handles scans |
|---|---|---|---|
| `markitdown` → text → model | cheap | no | no |
| `passthrough` → raw PDF → model | ~258 tok/page | yes | yes |

`passthrough` returns no text, so the extractor receives the raw bytes and a multimodal model sees the actual page. Use it when column alignment or stamps carry meaning. Config decides; no code changes.

## Install

```bash
uv venv && uv pip install -e ".[markitdown,llm,google,dev]"
```

Extras are independent, and adapters import their dependencies lazily. A folder→CSV pipeline runs with none of the optional extras installed; you only hit a missing dependency if you configure an adapter that needs it.

| Extra | Enables |
|---|---|
| `markitdown` | default normalizer |
| `docling` | OCR / table fallback normalizer (heavy) |
| `llm` | Gemini extractor |
| `google` | Gmail source, Drive store, Sheets sink |

## Run

```bash
docpipe list                          # registered adapters
docpipe validate pipelines/x.yaml     # config + env check, zero side effects
docpipe run pipelines/x.yaml --limit 3 --dry-run
docpipe run pipelines/x.yaml
```

`--dry-run` skips every side effect: no store writes, no sink writes, and the source is not marked processed. Safe to point at a live mailbox.

## Config

```yaml
name: vendor-invoices
limit: 50
on_error: skip            # skip | abort

source:
  type: gmail
  query: "has:attachment filename:pdf from:billing@"
  processed_label: docpipe/invoices
  filename_glob: "*.pdf"

store:
  type: gdrive
  path: "Invoices/{{yyyymm}}"

normalize:
  type: markitdown
  fallback:
    type: docling
    min_text_len: 200

extract:
  type: llm
  model: gemini-2.5-flash
  schema:
    invoice_no: string
    invoice_date: date
    vendor_name: string
    currency: currency
    total: number

sink:
  type: gsheet
  spreadsheet_id: ${INVOICE_SHEET_ID}
  tab: "{{year}}"
  dedupe_key: invoice_no
```

- `${VAR}` and `${VAR:-default}` interpolate from the environment, so the YAML holds no secrets and can be committed.
- `{{yyyymm}}`, `{{year}}`, `{{month}}`, `{{day}}`, `{{date}}`, `{{stem}}`, `{{uid}}` and any source metadata key (`{{sender}}`, `{{subject}}`) template Drive paths and sheet tabs.

### Exploding one document into many rows

For line items or statement transactions, name the array field:

```yaml
extract:
  type: llm
  records_path: line_items      # each element becomes its own row
  carry_fields: [invoice_no, invoice_date]   # copied onto every row
  schema:
    invoice_no: string
    invoice_date: date
    line_items:
      type: array
      items:
        description: string
        qty: number
        unit_price: number
```

## Idempotency

Three independent layers, because each catches a different failure:

1. **Source marking** — `mark_done` runs only *after* the sink write. A crash re-processes the tail rather than losing it. The Gmail source labels per message and excludes the label server-side via `-label:`, so already-handled mail is never fetched.
2. **Sink dedupe** — `dedupe_key` on a sink drops rows whose key already exists in the destination. This is what makes the re-processed tail harmless.
3. **Store skip** — the Drive store skips a filename that already exists in the target folder.

Set `dedupe_key` to a field from your schema (`invoice_no`) for business-level dedupe, or `_uid` (the content hash, added to every record) for exact-file dedupe.

## Free hosting

GitHub Actions cron. See `.github/workflows/run.yml`. Public repo = unlimited minutes; private = 2000 min/month, which is far more than this needs.

Secrets to set (Settings → Secrets and variables → Actions):

| Secret | From |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud console, OAuth desktop client |
| `GOOGLE_REFRESH_TOKEN` | `python scripts/google_oauth.py` |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey (free tier) |
| `INVOICE_SHEET_ID` | the Sheets URL |

A refresh token is used rather than a service account because a service account cannot read a personal Gmail mailbox without Workspace domain-wide delegation.

## Adding an adapter

One class, one decorator:

```python
from docpipe.core import Document, register

@register("sink", "postgres")
class PostgresSink:
    def __init__(self, dsn: str, table: str):
        ...
    def write(self, docs: list[Document]) -> int:
        ...
    def close(self) -> None:
        ...
```

Constructor keyword arguments *are* the config schema — every key under the stage in YAML is passed straight through, so a typo surfaces as a `TypeError` naming the bad key.

To ship adapters as a separate package, expose them under the `docpipe.adapters` entry point group; `load_plugins()` imports them at startup and a broken plugin is reported without failing the run.

## Layout

```
src/docpipe/
  core.py          Document, adapter protocols, registry, plugin loading
  config.py        YAML load, ${ENV} interpolation, validation
  pipeline.py      orchestrator — knows slot order, nothing about adapters
  templating.py    {{var}} rendering for paths and tab names
  google_auth.py   shared OAuth credentials for Gmail/Drive/Sheets
  sources/         gmail, local
  stores/          gdrive, local
  normalizers/     markitdown_, docling_, passthrough
  extractors/      llm (Gemini), regex, text
  sinks/           gsheet, csv_ (csv + jsonl)
```
