"""Command line entry point.

    docufunnel list [--describe]        # adapters, and every option each takes
    docufunnel doctor [config]          # what is installed, what is configured
    docufunnel schema [-o path]         # JSON Schema, for editor autocomplete
    docufunnel init [-o path]           # scaffold a pipeline
    docufunnel validate config          # config + env check, no side effects
    docufunnel run config [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import _ENV_RE, ConfigError, MissingEnvVar, load
from .core import SLOTS, available, load_plugins
from .introspect import (
    OPTIONAL_DEPS,
    describe,
    describe_all,
    json_schema,
    module_available,
)
from .pipeline import Pipeline

SCHEMA_FILE = ".docufunnel-schema.json"
SCHEMA_HEADER = f"# yaml-language-server: $schema=./{SCHEMA_FILE}"


def _load_dotenv() -> None:
    """Read a local .env if python-dotenv is installed. In CI the environment
    is already populated from secrets, so its absence is not an error.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (Path.cwd() / ".env", Path(__file__).parents[2] / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            return


# -- list -------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    load_plugins()
    if not args.describe:
        for slot, names in available().items():
            if args.slot and slot != args.slot:
                continue
            print(f"{slot:10} {', '.join(names) or '(none)'}")
        return 0

    for slot, docs in describe_all().items():
        if args.slot and slot != args.slot:
            continue
        print(f"\n{'=' * 72}\n{slot.upper()}\n{'=' * 72}")
        for doc in docs:
            flag = "" if doc.dependency_installed else f"  [needs {doc.requires}]"
            print(f"\n  {doc.name}{flag}")
            if doc.summary:
                print(f"    {doc.summary}")
            for opt in doc.options:
                print(f"      {opt.render()}")
    return 0


# -- doctor -----------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    from .google_auth import OAUTH_ENV, SA_ENV, auth_mode

    load_plugins()
    problems = 0

    print("optional dependencies")
    for module, extra in sorted(OPTIONAL_DEPS.items()):
        if module_available(module):
            print(f"  {module:24} installed")
        else:
            print(f'  {module:24} missing   -> pip install "docufunnel[{extra}]"')

    print("\ngoogle credentials")
    mode = auth_mode()
    if mode == "service_account":
        print(f"  {SA_ENV} set (no OAuth app needed; Drive/Sheets only)")
    elif mode == "oauth":
        print("  OAuth refresh token set (Gmail, Drive and Sheets available)")
    else:
        print(f"  none. Set {SA_ENV}, or {', '.join(OAUTH_ENV)}")
        print("  Only needed if a pipeline uses gmail, gdrive or gsheet.")

    if not args.config:
        print("\npass a config path to check the variables it actually references")
        return 0

    path = Path(args.config)
    if not path.exists():
        print(f"\nconfig not found: {path}", file=sys.stderr)
        return 2

    text = path.read_text()
    names = sorted({m.group(1) for m in _ENV_RE.finditer(text) if not m.group(2)})
    if names:
        print(f"\nvariables referenced by {path.name}")
        import os

        for name in names:
            # An empty value counts as unset: that is how an unconfigured
            # GitHub secret arrives.
            if os.environ.get(name):
                print(f"  {name:28} set")
            else:
                print(f"  {name:28} MISSING")
                problems += 1

    try:
        cfg = load(path)
    except (MissingEnvVar, ConfigError, ValueError, TypeError) as exc:
        print(f"\nconfig error: {exc}", file=sys.stderr)
        return 1

    print(f"\nadapters used by {cfg.name}")
    for slot in SLOTS:
        stage = getattr(cfg, slot)
        if stage is None:
            print(f"  {slot:10} -")
            continue
        doc = describe(slot, stage.type)
        if doc.dependency_installed:
            print(f"  {slot:10} {stage.type:14} ready")
        else:
            extra = OPTIONAL_DEPS.get(doc.requires or "", "?")
            print(
                f"  {slot:10} {stage.type:14} MISSING {doc.requires}"
                f' -> pip install "docufunnel[{extra}]"'
            )
            problems += 1

    print(f"\n{problems} problem(s)" if problems else "\nall good")
    return 1 if problems else 0


# -- schema -----------------------------------------------------------------


def _cmd_schema(args: argparse.Namespace) -> int:
    load_plugins()
    schema = json_schema()
    if args.output == "-":
        print(json.dumps(schema, indent=2))
        return 0
    out = Path(args.output)
    out.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {out}")
    print(f"add this line to the top of a pipeline for editor autocomplete:\n  {SCHEMA_HEADER}")
    return 0


# -- init -------------------------------------------------------------------

# A worked body per adapter. Generating one from the signature alone produced
# nonsense (`fields: ${FIELDS}` for the regex extractor) and, worse, picked
# placeholder names that collide with real environment variables — a bare
# `path: ${PATH}` interpolates the shell's PATH. So the useful shape is
# written out here, and anything required but not covered becomes a TODO.
HINTS: dict[tuple[str, str], list[str]] = {
    ("source", "imap"): [
        "user: ${IMAP_USER}",
        "# A Gmail app password, not the account password:",
        "# https://myaccount.google.com/apppasswords",
        "password: ${IMAP_PASSWORD}",
        "mailbox: INBOX",
        '# Gmail search syntax, via the IMAP X-GM-RAW extension.',
        'gmail_search: "has:attachment filename:pdf"',
        'filename_glob: "*.pdf"',
        "processed: keyword",
    ],
    ("source", "local"): ["path: ./samples", 'glob: "**/*.pdf"', "state_file: .state/seen.json"],
    ("source", "gmail"): [
        '# Needs OAuth credentials; prefer the imap source if you can.',
        'query: "has:attachment filename:pdf"',
        "processed_label: docufunnel/done",
        'filename_glob: "*.pdf"',
    ],
    ("store", "local"): ['path: archive/{{yyyymm}}'],
    ("store", "gdrive"): [
        'path: "Archive/{{yyyymm}}"',
        "# A service account has no Drive quota of its own; this needs OAuth",
        "# on a personal account.",
    ],
    ("normalize", "markitdown"): [
        "# markitdown has no OCR: a scanned PDF converts to near-empty text",
        "# without raising, so the fallback triggers on length.",
        "fallback:",
        "  type: docling",
        "  min_text_len: 200",
    ],
    ("normalize", "docling"): ["ocr: true", "table_mode: accurate"],
    ("normalize", "passthrough"): [
        "# No text extracted: the raw file goes to the model, which keeps",
        "# layout and handles scans, at ~258 tokens per page.",
    ],
    ("extract", "llm"): [
        "model: gemini-2.5-flash",
        "schema:",
        "  invoice_no: string",
        "  invoice_date: date",
        "  vendor_name: string",
        "  currency: currency",
        "  total: number",
    ],
    ("extract", "regex"): [
        "fields:",
        # Single-quoted: YAML processes escapes inside double quotes, and
        # \\s is not a valid one, so a double-quoted regex fails to parse.
        "  invoice_no: 'Invoice\\s*#:\\s*(\\S+)'",
        "  total:",
        "    pattern: '^Total:\\s*([\\d,.]+)'",
        "    cast: number",
    ],
    ("extract", "text"): ["max_chars: 8000"],
    ("sink", "csv"): ["path: out/records.csv", "dedupe_key: _uid"],
    ("sink", "jsonl"): ["path: out/records.jsonl", "dedupe_key: _uid"],
    ("sink", "gsheet"): [
        "# Share the sheet with your service account's client_email as Editor.",
        "spreadsheet_id: ${SHEET_ID}",
        'tab: "{{year}}"',
        "dedupe_key: _uid",
    ],
}


def _slot_of(options: list[str]) -> str:
    """Which slot a set of adapter names belongs to, so the chooser can show
    each option's one-line summary.
    """
    for slot, names in available().items():
        if names == options:
            return slot
    return "source"


def _choose(prompt: str, options: list[str], default: str) -> str:
    slot = _slot_of(options)
    print(f"\n{prompt}")
    for i, name in enumerate(options, 1):
        mark = " (default)" if name == default else ""
        print(f"  {i}. {name}{mark}  {describe(slot, name).summary[:58]}")
    raw = input(f"choice [{default}]: ").strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    if raw in options:
        return raw
    print(f"not one of {options}, using {default}")
    return default


def _stage_yaml(slot: str, name: str) -> list[str]:
    """Emit a stage block: the worked hint body, plus a TODO for any required
    option the hint does not already set.
    """
    body = HINTS.get((slot, name), [])
    # Only real keys count as "covered" — comment lines must not suppress a TODO.
    covered = {
        line.split(":", 1)[0].strip()
        for line in body
        if ":" in line and not line.lstrip().startswith("#") and not line.startswith(" ")
    }
    todos = [
        f"  # TODO: {opt.name}: <{opt.type_name}>  (required)"
        for opt in describe(slot, name).options
        if opt.required and opt.name not in covered
    ]
    return [f"{slot}:", f"  type: {name}", *todos, *(f"  {line}" for line in body)]


def _cmd_init(args: argparse.Namespace) -> int:
    load_plugins()
    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"{out} exists; pass --force to overwrite", file=sys.stderr)
        return 2

    if args.non_interactive:
        source, normalize = args.source, args.normalize
        extract, sink = args.extract, args.sink
    else:
        print("Scaffolding a pipeline. Enter to accept each default.")
        source = _choose("source — where documents come from", available("source")["source"], args.source)
        normalize = _choose(
            "normalize — bytes to text ('passthrough' sends the raw file to the model)",
            available("normalize")["normalize"],
            args.normalize,
        )
        extract = _choose("extract — text to rows", available("extract")["extract"], args.extract)
        sink = _choose("sink — where rows go", available("sink")["sink"], args.sink)

    chosen = (("source", source), ("normalize", normalize), ("extract", extract), ("sink", sink))
    for slot, name in chosen:
        if name not in available(slot)[slot]:
            print(f"unknown {slot}: {name!r}", file=sys.stderr)
            return 2

    blocks: list[list[str]] = [
        [SCHEMA_HEADER, f"name: {out.stem}", "", "limit: 20", "on_error: skip"]
    ]
    blocks.extend(_stage_yaml(slot, name) for slot, name in chosen)
    text = "\n\n".join("\n".join(b) for b in blocks) + "\n"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)

    schema_path = out.parent / SCHEMA_FILE
    if not schema_path.exists():
        schema_path.write_text(json.dumps(json_schema(), indent=2) + "\n")

    print(f"\nwrote {out}")
    todos = text.count("# TODO:")
    if todos:
        print(f"{todos} required option(s) left as TODO — fill them in.")
    print("Placeholders are ${UPPER_CASE}: set them in .env or as CI secrets.")
    print(f"Next:\n  docufunnel doctor {out}\n  docufunnel run {out} --limit 3 --dry-run")
    return 0


# -- validate / run ---------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config)
    except (MissingEnvVar, ConfigError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    # Constructing the Pipeline resolves every adapter name and runs each
    # adapter's __init__ validation without touching a network or filesystem.
    Pipeline(cfg)
    print(f"ok: {cfg.name}")
    for slot in SLOTS:
        stage = getattr(cfg, slot)
        print(f"  {slot:10} {stage.type if stage else '-'}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config)
    except (MissingEnvVar, ConfigError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    overrides = {}
    if args.limit:
        overrides["limit"] = args.limit
    if args.dry_run:
        overrides["dry_run"] = True
    if args.abort_on_error:
        overrides["on_error"] = "abort"
    if overrides:
        cfg = cfg.model_copy(update=overrides)

    result = Pipeline(cfg).run()
    print(result.summary())
    # Non-zero when nothing landed but documents were seen, so a cron job that
    # silently stops extracting shows up as a failed run instead of a green one.
    if result.fetched and not result.written and not cfg.dry_run:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docufunnel",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list adapters")
    p_list.add_argument("--describe", action="store_true", help="show every option each takes")
    p_list.add_argument("--slot", choices=SLOTS)
    p_list.set_defaults(func=_cmd_list)

    p_doc = sub.add_parser("doctor", help="check dependencies, credentials and a config")
    p_doc.add_argument("config", nargs="?")
    p_doc.set_defaults(func=_cmd_doctor)

    p_sch = sub.add_parser("schema", help="write a JSON Schema for editor autocomplete")
    p_sch.add_argument("-o", "--output", default=SCHEMA_FILE, help="'-' for stdout")
    p_sch.set_defaults(func=_cmd_schema)

    p_init = sub.add_parser("init", help="scaffold a pipeline config")
    p_init.add_argument("-o", "--output", default="pipelines/my-pipeline.yaml")
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--non-interactive", action="store_true")
    p_init.add_argument("--source", default="imap")
    p_init.add_argument("--normalize", default="markitdown")
    p_init.add_argument("--extract", default="llm")
    p_init.add_argument("--sink", default="gsheet")
    p_init.set_defaults(func=_cmd_init)

    p_val = sub.add_parser("validate", help="check a pipeline config")
    p_val.add_argument("config")
    p_val.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="run a pipeline")
    p_run.add_argument("config")
    p_run.add_argument("--limit", type=int, help="process at most N documents")
    p_run.add_argument("--dry-run", action="store_true", help="no writes, no source marking")
    p_run.add_argument("--abort-on-error", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    _load_dotenv()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
