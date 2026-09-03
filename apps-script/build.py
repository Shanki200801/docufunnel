#!/usr/bin/env python3
"""Concatenate src/*.gs into dist/docufunnel.gs.

Apps Script files share one global scope, so concatenation is semantically
identical to the multi-file project. The single file exists because the
realistic install path for a non-technical user is pasting one blob into the
script editor, and keeping a hand-maintained copy in sync would rot.

Order comes from the numeric filename prefix.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
OUT = HERE / "dist" / "docufunnel.gs"

BANNER = """/**
 * DocuFunnel — Apps Script edition
 *
 * GENERATED FILE — do not edit. Built from apps-script/src/*.gs by build.py.
 * Source: https://github.com/Shanki200801/docufunnel
 *
 * Paste this whole file into a spreadsheet's Apps Script editor (Extensions >
 * Apps Script), save, reload the spreadsheet, then use the DocuFunnel menu.
 */

"""


def main() -> int:
    parts = [BANNER]
    files = sorted(SRC.glob("*.gs"))
    if not files:
        print(f"no .gs files in {SRC}", file=sys.stderr)
        return 1

    for path in files:
        parts.append(f"// {'=' * 74}\n// {path.name}\n// {'=' * 74}\n\n")
        parts.append(path.read_text().rstrip() + "\n\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(parts))
    print(f"wrote {OUT.relative_to(HERE.parent)}  ({OUT.stat().st_size:,} bytes, {len(files)} files)")

    # A syntax error here would only surface after a user pasted the file in,
    # so check it while we still can. node --check refuses a .gs extension, so
    # the check runs against a temporary copy.
    node = which("node")
    if node is None:
        print("node not found; skipped the syntax check")
        return 0

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
        tmp.write(OUT.read_text())
        probe = tmp.name
    try:
        check = subprocess.run([node, "--check", probe], capture_output=True, text=True)
    finally:
        Path(probe).unlink(missing_ok=True)

    if check.returncode != 0:
        print(check.stderr.replace(probe, str(OUT)), file=sys.stderr)
        return 1
    print("syntax ok (node --check)")
    return 0


def which(cmd: str) -> str | None:
    from shutil import which as _which

    return _which(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
