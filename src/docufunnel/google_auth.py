"""Shared Google credentials for the Drive / Sheets / Gmail adapters.

Two auth modes, picked from the environment. The distinction is not a
convenience — it decides whether a user needs a Google Cloud OAuth app at all.

**Service account** (`GOOGLE_SERVICE_ACCOUNT_JSON`) — preferred where it
works. Google's own verification rules exempt it: an app using a service
account to reach only its own data does not need to be submitted for
verification. There is no consent screen, no unverified-app warning, and no
token that expires. The user shares a Sheet (or Drive folder) with the service
account's email address and that is the whole setup.

**OAuth refresh token** (`GOOGLE_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN`) —
required for Gmail, because a service account cannot read a personal mailbox
without Workspace domain-wide delegation. Costs the user a Cloud project and a
click through the unverified-app screen. Prefer the `imap` source instead.

Two limits worth knowing before choosing:

* Gmail is unreachable with a service account. Use `imap`, or OAuth.
* A service account has no Drive storage quota of its own, so uploading with
  the `gdrive` store can fail with `storageQuotaExceeded` on a personal
  account. Appending rows to a user-owned Sheet consumes no storage and is
  unaffected. If you need the Drive store on a personal account, use OAuth or
  store elsewhere (the store slot is optional).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

TOKEN_URI = "https://oauth2.googleapis.com/token"

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

# Requested by scripts/google_oauth.py. gmail.modify is a *restricted* scope:
# a shared app using it needs Google verification plus an annual CASA security
# assessment, which is why this project never ships its own OAuth client.
SCOPES = [GMAIL_SCOPE, DRIVE_SCOPE, SHEETS_SCOPE]

# A service account cannot hold the Gmail scope usefully, so it is omitted.
SERVICE_ACCOUNT_SCOPES = [DRIVE_SCOPE, SHEETS_SCOPE]

SA_ENV = "GOOGLE_SERVICE_ACCOUNT_JSON"
OAUTH_ENV = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")

_SA_APIS = {"drive", "sheets"}


class MissingCredentials(RuntimeError):
    pass


def auth_mode() -> str | None:
    """Which credential set the environment provides: 'service_account',
    'oauth', or None. Used by `docufunnel doctor` and for error messages.
    """
    if os.environ.get(SA_ENV):
        return "service_account"
    if all(os.environ.get(k) for k in OAUTH_ENV):
        return "oauth"
    return None


def _service_account_info() -> dict[str, Any]:
    """Accept either the JSON itself or a path to it, since a GitHub secret
    holds the blob while a local .env more naturally holds a path.
    """
    raw = os.environ[SA_ENV].strip()
    if raw.startswith("{"):
        return json.loads(raw)
    path = Path(raw).expanduser()
    if not path.exists():
        raise MissingCredentials(
            f"{SA_ENV} is neither JSON nor an existing file path: {raw!r}"
        )
    return json.loads(path.read_text())


@lru_cache(maxsize=4)
def credentials(scopes: tuple[str, ...] | None = None) -> Any:
    mode = auth_mode()

    if mode == "service_account":
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_info(
            _service_account_info(), scopes=list(scopes or SERVICE_ACCOUNT_SCOPES)
        )

    if mode == "oauth":
        from google.oauth2.credentials import Credentials

        return Credentials(
            token=None,
            refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            token_uri=TOKEN_URI,
            scopes=list(scopes or SCOPES),
        )

    missing = [k for k in OAUTH_ENV if not os.environ.get(k)]
    raise MissingCredentials(
        f"no Google credentials. Either set {SA_ENV} (simplest — no OAuth app "
        f"needed, works for Drive and Sheets), or set {', '.join(missing)} "
        f"via: python scripts/google_oauth.py"
    )


@lru_cache(maxsize=8)
def service(api: str, version: str) -> Any:
    """Cached googleapiclient service.

    cache_discovery is off because it warns noisily and writes to a cache
    directory that does not exist in CI.
    """
    from googleapiclient.discovery import build

    if api == "gmail" and auth_mode() == "service_account":
        raise MissingCredentials(
            "Gmail cannot be reached with a service account (that needs Workspace "
            "domain-wide delegation). Use the `imap` source, which needs only an "
            "app password, or supply OAuth credentials."
        )

    scopes = tuple(SERVICE_ACCOUNT_SCOPES) if api in _SA_APIS else None
    return build(api, version, credentials=credentials(scopes), cache_discovery=False)
