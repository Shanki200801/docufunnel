# docufunnel

Get structured data out of documents that arrive as attachments, without writing a new script per use case.

```
SOURCE ──▶ STORE ──▶ NORMALIZE ──▶ EXTRACT ──▶ SINK
 Gmail     Drive      MarkItDown     Gemini      Sheets
 folder    folder     Docling        regex       CSV / JSONL
                      passthrough    text
```

Every slot is an adapter picked by a `type:` string in YAML. Swapping Gmail for a watched folder, or Sheets for a CSV, is a config edit — not a code change. Runs free on GitHub Actions cron.

## Two editions

Pick by who is going to run it.

| | [Apps Script edition](apps-script/) | Python edition (this page) |
|---|---|---|
| For | anyone, including non-technical users | developers, and anything at volume |
| Install | **none** — copy a Google Sheet | Python + pip/uv |
| Gmail access | **nothing to set up** — runs as the user | app password or OAuth |
| Secrets to paste | **1** (Gemini key) | 3–5 |
| Config lives in | two spreadsheet tabs | YAML in git |
| Scheduling | built-in triggers | GitHub Actions cron |
| Formats | PDF and images | also DOCX, XLSX, PPTX, HTML, CSV, EPub |
| Scanned pages | Gemini reads them directly | Docling OCR fallback |
| Rows per document | one | many (`records_path`) |
| Runtime cap | ~6 min per run | none |

Apps Script needs one secret because it already runs **as the signed-in
user** — no OAuth client, no app password, no service account to read that
person's mail or write their sheet. Both editions use the same five stages, so
moving up costs no relearning.

## Quickstart

### As a scheduled job — no install

Click **Use this template** on GitHub. You get a repo with the cron already
wired. Then:

1. **Settings → Secrets and variables → Actions → Secrets**, add three:
   - `IMAP_USER` — your Gmail address
   - `IMAP_PASSWORD` — an [app password](https://myaccount.google.com/apppasswords) (needs 2-Step Verification on)
   - `GEMINI_API_KEY` — free from [AI Studio](https://aistudio.google.com/apikey)
2. Create a Google Sheet. Add `INVOICE_SHEET_ID` (the id from its URL) as a
   fourth secret.
3. For the Sheet, either add `GOOGLE_SERVICE_ACCOUNT_JSON` and share the sheet
   with the account's `client_email` as Editor, or swap the sink to
   `type: csv` and skip Google entirely.
4. Edit `pipelines/imap-invoices.yaml` — the `gmail_search` line and the
   `schema:` block are the two things worth changing.
5. **Actions → run → Run workflow**, tick **dry_run**. Nothing is written and
   no mail is marked, so it is safe against a live mailbox. Read the log,
   then untick it.

The schedule takes over from there.

### As a library or CLI

```bash
pip install "docufunnel[recommended]"      # or: uv pip install ...
docufunnel setup                           # asks a few questions, writes a pipeline
docufunnel run pipelines/my-documents.yaml --limit 3 --dry-run
```

`setup` verifies every credential as you type it — a bad Gemini key or a Gmail
account password used where an app password is needed fails there, not as a
silent cron failure hours later. It asks what to extract in plain English (the
same idea as the Apps Script Fields tab), picks a sensible duplicate-check
field, and writes `.env`, the pipeline, and the editor schema.

```python
from docufunnel import run_file
result = run_file("pipelines/imap-invoices.yaml", limit=5, dry_run=True)
print(result.summary())
```


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
uv venv && uv pip install -e ".[recommended,dev]"
```

`recommended` is `markitdown` + `llm` + `google`. For contributing, add `dev`.

Extras are independent, and adapters import their dependencies lazily. A folder→CSV pipeline runs with none of the optional extras installed; you only hit a missing dependency if you configure an adapter that needs it.

| Extra | Enables |
|---|---|
| `markitdown` | default normalizer |
| `docling` | OCR / table fallback normalizer (heavy) |
| `llm` | Gemini extractor |
| `google` | Gmail source, Drive store, Sheets sink |

## Run

```bash
docufunnel setup                         # guided: verifies keys, writes everything
docufunnel init                          # bare scaffold, no questions about credentials
docufunnel list --describe               # every adapter and every option it takes
docufunnel doctor pipelines/x.yaml       # what is missing before you run
docufunnel schema                        # JSON Schema for editor autocomplete
docufunnel validate pipelines/x.yaml     # config + env check, zero side effects
docufunnel run pipelines/x.yaml --limit 3 --dry-run
docufunnel run pipelines/x.yaml
```

`--dry-run` skips every side effect: no store writes, no sink writes, and the source is not marked processed. Safe to point at a live mailbox.

### The config is the UI, so it documents itself

An adapter's constructor keyword arguments *are* its config schema. `list
--describe`, `doctor` and the JSON Schema are all derived from those
signatures, so there is no second copy to drift:

```
$ docufunnel list --describe --slot source
  imap
    IMAP source — the adapter that makes this tool distributable.
      user: str  (required)
      password: str  (required)
      host: str = 'imap.gmail.com'
      port: int = 993
      gmail_search: str | None = None
      ...
```

`docufunnel schema` writes `.docufunnel-schema.json`. Every shipped pipeline
starts with

```yaml
# yaml-language-server: $schema=../.docufunnel-schema.json
```

which gives completion, hover docs and inline validation in any editor with
the YAML language server — including only the options that the `type:` you
picked actually accepts. A test asserts the schema accepts every pipeline in
`pipelines/`, so it cannot quietly rot.

`docufunnel doctor` answers "why did that not work" before you run:

```
$ docufunnel doctor pipelines/imap-invoices.yaml
optional dependencies
  markitdown               installed
  docling                  missing   -> pip install "docufunnel[docling]"
google credentials
  GOOGLE_SERVICE_ACCOUNT_JSON set (no OAuth app needed; Drive/Sheets only)
variables referenced by imap-invoices.yaml
  IMAP_PASSWORD                set
  INVOICE_SHEET_ID             MISSING
adapters used by imap-invoices
  source     imap           ready
  extract    llm            ready
```

## Config

```yaml
name: vendor-invoices
limit: 50
on_error: skip            # skip | abort

source:
  type: gmail
  query: "has:attachment filename:pdf from:billing@"
  processed_label: docufunnel/invoices
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

## Auth: pick a route before anything else

This decides whether you need a Google Cloud project at all.

### Route A — app password + service account (recommended)

Nothing to verify, nothing that expires, no consent screen.

| Need | How | Time |
|---|---|---|
| Read mail | Gmail **app password** → `imap` source | 2 min |
| Write rows | **Service account** → share the Sheet with its `client_email` | 3 min |

The `imap` source uses only the standard library — no extra to install.

### Route B — OAuth (only if you need the Gmail API)

Buys server-side label writes and Gmail API search. Costs a Cloud project, an
OAuth client, and a click through an unverified-app screen.

**You cannot publish a shared OAuth app for this.** `gmail.modify` is a
[restricted scope](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification):
distributing one app that many people authorise requires Google verification
**plus an annual CASA security assessment**, redone every 12 months. Leaving the
app in *Testing* instead caps users, shows a warning screen, and limits the
refresh-token lifetime — which breaks a cron job within a week. Google's
personal-use exception covers you and a few people you know personally,
clicking past the unverified screen with their own client.

So every user brings their own credentials. That is the only shape this can
legally take, and it is why Route A exists.

Two limits that follow from how service accounts work:

- **Gmail is unreachable with a service account** (that needs Workspace
  domain-wide delegation). Use `imap`, or Route B.
- **A service account has no Drive storage quota of its own**, so the `gdrive`
  store can fail with `storageQuotaExceeded` on a personal account. Appending
  rows to a user-owned Sheet consumes no storage and is unaffected. The store
  slot is optional — drop it, or use a local store.

Never commit a client secret or a service-account key. `.env` is gitignored;
in CI use repository secrets.

## Free hosting

GitHub Actions cron. See `.github/workflows/run.yml`. Public repo = unlimited minutes; private = 2000 min/month, which is far more than this needs.

Secrets to set (Settings → Secrets and variables → Actions):

Route A secrets:

| Secret | From |
|---|---|
| `IMAP_USER` / `IMAP_PASSWORD` | your address + https://myaccount.google.com/apppasswords |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Cloud console → service account → JSON key (paste the whole blob) |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey (free tier) |
| `INVOICE_SHEET_ID` | the Sheets URL |

Route B swaps the first two for `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`GOOGLE_REFRESH_TOKEN` (see `scripts/google_oauth.py`).

## Adding an adapter

One class, one decorator:

```python
from docufunnel.core import Document, register

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

To ship adapters as a separate package, expose them under the `docufunnel.adapters` entry point group; `load_plugins()` imports them at startup and a broken plugin is reported without failing the run.

## Layout

```
src/docufunnel/
  core.py          Document, adapter protocols, registry, plugin loading
  config.py        YAML load, ${ENV} interpolation, validation
  pipeline.py      orchestrator — knows slot order, nothing about adapters
  templating.py    {{var}} rendering for paths and tab names
  introspect.py    signatures -> docs, JSON Schema, doctor checks
  google_auth.py   service-account or OAuth credentials for Drive/Sheets/Gmail
  sources/         imap, gmail, local
  stores/          gdrive, local
  normalizers/     markitdown_, docling_, passthrough
  extractors/      llm (Gemini), regex, text
  sinks/           gsheet, csv_ (csv + jsonl)
```
