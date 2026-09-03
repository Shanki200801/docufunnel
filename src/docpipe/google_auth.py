"""Shared Google credentials for the Gmail / Drive / Sheets adapters.

A long-lived OAuth refresh token is used rather than a service account: a
service account cannot read a personal Gmail mailbox without Workspace
domain-wide delegation. Mint the token once with scripts/google_oauth.py and
store it as a secret; it is then the only Google state CI needs.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

TOKEN_URI = "https://oauth2.googleapis.com/token"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read + apply labels
    "https://www.googleapis.com/auth/drive.file",  # only files this app creates
    "https://www.googleapis.com/auth/spreadsheets",
]


class MissingCredentials(RuntimeError):
    pass


@lru_cache(maxsize=1)
def credentials() -> Any:
    from google.oauth2.credentials import Credentials

    missing = [
        k
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
        if not os.environ.get(k)
    ]
    if missing:
        raise MissingCredentials(
            f"missing {', '.join(missing)}. Run: python scripts/google_oauth.py"
        )

    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )


@lru_cache(maxsize=8)
def service(api: str, version: str) -> Any:
    """Cached googleapiclient service. cache_discovery is off because it warns
    noisily and writes to a cache dir that does not exist in CI.
    """
    from googleapiclient.discovery import build

    return build(api, version, credentials=credentials(), cache_discovery=False)
