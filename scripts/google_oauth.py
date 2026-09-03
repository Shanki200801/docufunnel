#!/usr/bin/env python3
"""One-time: mint a long-lived Google refresh token for CI.

Prerequisite — in the Google Cloud console:
  1. Create (or pick) a project.
  2. Enable the Gmail, Drive and Sheets APIs.
  3. OAuth consent screen: External, and add your own address under Test users.
     A token from an app left in "Testing" expires after 7 days, so once it
     works, hit Publish. Publishing needs no review while the app requests only
     your own data and has no other users.
  4. Credentials -> Create credentials -> OAuth client ID -> Desktop app.

Then run:
    python scripts/google_oauth.py --client-secret ~/Downloads/client_secret_*.json
or, if you would rather not keep the JSON around:
    GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python scripts/google_oauth.py

The refresh token is printed once. Put it in GitHub Actions secrets as
GOOGLE_REFRESH_TOKEN alongside the client id and secret.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docpipe.google_auth import SCOPES


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-secret", help="path to the downloaded OAuth client JSON")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print('install the extra first:  uv pip install -e ".[google]"', file=sys.stderr)
        return 2

    if args.client_secret:
        flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    else:
        cid, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
        if not (cid and secret):
            print(
                "pass --client-secret, or set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET",
                file=sys.stderr,
            )
            return 2
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": cid,
                    "client_secret": secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            },
            SCOPES,
        )

    print("Scopes requested:")
    for s in SCOPES:
        print(f"  {s}")
    print("\nOpening a browser. Approve the consent screen.\n")

    # access_type=offline plus prompt=consent is what guarantees a refresh
    # token comes back; Google omits it on re-consent otherwise.
    creds = flow.run_local_server(
        port=args.port, access_type="offline", prompt="consent", open_browser=True
    )

    if not creds.refresh_token:
        print("no refresh token returned — revoke the app's access and retry", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print("GOOGLE_CLIENT_ID     =", flow.client_config["client_id"])
    print("GOOGLE_CLIENT_SECRET =", flow.client_config["client_secret"])
    print("GOOGLE_REFRESH_TOKEN =", creds.refresh_token)
    print("=" * 60)

    env = Path(__file__).resolve().parents[1] / ".env"
    if input(f"\nwrite these to {env}? [y/N] ").strip().lower() == "y":
        existing = env.read_text() if env.exists() else ""
        lines = [
            ln
            for ln in existing.splitlines()
            if not ln.startswith(("GOOGLE_CLIENT_ID=", "GOOGLE_CLIENT_SECRET=", "GOOGLE_REFRESH_TOKEN="))
        ]
        lines += [
            f"GOOGLE_CLIENT_ID={flow.client_config['client_id']}",
            f"GOOGLE_CLIENT_SECRET={flow.client_config['client_secret']}",
            f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}",
        ]
        env.write_text("\n".join(lines) + "\n")
        print(f"wrote {env} (already in .gitignore)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
