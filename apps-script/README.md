# DocuFunnel — Apps Script edition

Mail with attachments in, structured rows out, in a Google Sheet. Nothing to
install.

## Why this edition exists

Apps Script runs **as the signed-in user**. It needs no OAuth client, no app
password and no service account to read that person's mail or write their
sheet. So the entire setup is one pasted API key.

| | Apps Script edition | [Python edition](../README.md) |
|---|---|---|
| Install | none | Python + pip/uv |
| Gmail access | **none to set up** | app password or OAuth |
| Drive / Sheets access | **none to set up** | service account or OAuth |
| Secrets to paste | **1** (Gemini key) | 3–5 |
| Scheduling | built in | GitHub Actions |
| Extraction | Gemini reads the PDF directly | + MarkItDown / Docling, OCR fallback |
| Formats | PDF and images | also DOCX, XLSX, PPTX, HTML, CSV, EPub |
| Runtime cap | ~6 min per run | none |
| Config | two spreadsheet tabs | YAML in git |

There is no normalize stage here, deliberately. Gemini reads a PDF natively, so
page layout survives and scanned documents work without a separate OCR step —
which is also why this edition needs no MarkItDown equivalent. It costs roughly
258 tokens per page; the free tier absorbs that at these volumes.

## Setup (for whoever is going to use it)

1. Open the spreadsheet you were given and **File → Make a copy**.
2. Reload the copy. A **DocuFunnel** menu appears next to Help.
3. **DocuFunnel → Set up**. It will ask for a Gemini API key — get one free in
   about thirty seconds at <https://aistudio.google.com/apikey> ("Create API
   key"), and paste it in. The key is checked immediately, so a typo shows up
   now rather than as a silent failure later.
4. First time only, Google asks you to authorize the script. Because this copy
   is yours and unpublished, you will see **"Google hasn't verified this app"** —
   click **Advanced → Go to … (unsafe)**. You are authorizing your own copy to
   read your own mail; nothing is sent anywhere except to Gemini for the
   extraction.
5. Look at two tabs:
   - **Settings** — which emails to look at, where to save things. Each row has
     a plain-English note beside it.
   - **Fields** — what to pull out of each document. The third column is a note
     in plain English telling the AI what the field means. That is how you
     configure extraction: no code.
6. **DocuFunnel → Test run**. It reads your mail and shows what it found
   *without* saving anything or marking any email as done. Safe to repeat.
7. Happy with the results? **Run now**, then **Run automatically every hour**.

If something looks wrong, **DocuFunnel → Check my setup** tells you what is
missing, and the **Log** tab has the detail.

## Setup (for whoever is publishing the template)

Once, to produce the copyable spreadsheet:

1. Create a new Google Sheet, name it something like *DocuFunnel — Invoices*.
2. **Extensions → Apps Script**.
3. Paste all of [`dist/docufunnel.gs`](dist/docufunnel.gs) into `Code.gs`,
   replacing what is there. Save.
4. Project Settings → **Show "appsscript.json"**, then paste
   [`appsscript.json`](appsscript.json). Adjust `timeZone`.
5. Back in the sheet, reload, and run **DocuFunnel → Set up** once to create
   the tabs and check the script works end to end.
6. **Delete your own API key** before sharing: Apps Script editor → Project
   Settings → Script Properties → remove `GEMINI_API_KEY`. Script properties do
   not travel with a copy, but do not rely on that — check.
7. Clear the **Data** and **Log** tabs so nobody inherits your rows.
8. Share → **Anyone with the link → Viewer**, and hand out the link with
   `/edit` replaced by `/copy`. That URL opens straight into "Make a copy".

### Maintaining it

Edit `src/*.gs`, never `dist/docufunnel.gs`:

```bash
python3 apps-script/build.py     # concatenates src/ -> dist/, syntax-checks it
```

Apps Script files share one global scope, so concatenating them is identical to
the multi-file project. The single file exists because pasting one blob is the
realistic install path, and a hand-maintained copy would drift. CI fails if
`dist/` is out of date.

To push straight to a script project instead of pasting, use
[`clasp`](https://github.com/google/clasp):

```bash
npm i -g @google/clasp && clasp login
cd apps-script && clasp clone <SCRIPT_ID>   # writes .clasp.json (gitignored)
clasp push
```

`clasp` pushes `src/*.gs` directly — no build step needed for that path.

## Limits worth knowing before you rely on it

- **~6 minutes per run.** The script stops itself at 4.5 minutes and logs that
  it did; the next run picks up where it left off, because an email is only
  labelled once its rows are safely in the sheet. Keep *Max emails per run*
  modest.
- **One row per document.** Exploding an invoice's line items into a row each
  is a Python-edition feature (`records_path`).
- **PDF and images only.** Other formats need the Python edition's normalize
  stage.
- **Gemini free-tier rate limits** apply. The script backs off and retries
  transient 429s and 5xx, and logs anything it gives up on.

When you outgrow these, the Python edition takes the same five stages with
adapters you can swap, and nothing you learned here is wasted.

## What runs where

| Stage | Apps Script edition |
|---|---|
| source | `GmailApp.search`, with `-label:` exclusion so processed mail is never re-downloaded |
| store | `DriveApp`, into `{{yyyy-mm}}` folders, skipping a filename already there |
| normalize | *(none — the raw file goes to the model)* |
| extract | Gemini `generateContent`, constrained to a schema built from the Fields tab |
| sink | the output tab, header treated as yours, duplicates dropped by the chosen field |
