"""Command line entry point.

    docpipe list                       # what adapters are available
    docpipe validate pipelines/x.yaml  # config + env check, no side effects
    docpipe run pipelines/x.yaml --limit 3 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import MissingEnvVar, load
from .core import available, load_plugins
from .pipeline import Pipeline


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


def _cmd_list(_: argparse.Namespace) -> int:
    load_plugins()
    for slot, names in available().items():
        print(f"{slot:10} {', '.join(names) or '(none)'}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load(args.config)
    except MissingEnvVar as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    # Constructing the Pipeline resolves every adapter name and runs each
    # adapter's __init__ validation without touching a network or filesystem.
    Pipeline(cfg)
    print(f"ok: {cfg.name}")
    for slot in ("source", "store", "normalize", "extract", "sink"):
        stage = getattr(cfg, slot)
        print(f"  {slot:10} {stage.type if stage else '-'}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = load(args.config)
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
    parser = argparse.ArgumentParser(prog="docpipe", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list registered adapters").set_defaults(func=_cmd_list)

    p_val = sub.add_parser("validate", help="check a pipeline config")
    p_val.add_argument("config")
    p_val.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="run a pipeline")
    p_run.add_argument("config")
    p_run.add_argument("--limit", type=int, help="process at most N documents")
    p_run.add_argument(
        "--dry-run", action="store_true", help="no writes, no source marking"
    )
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
