"""Credential-selection tests.

Which auth mode is picked decides whether a user needs a Google Cloud OAuth
app at all, so the selection logic and its error messages are pinned down
here. No real credential is constructed — the SDK call is stubbed.
"""

from __future__ import annotations

import json

import pytest

from docufunnel import google_auth as ga

ALL_ENV = (ga.SA_ENV, *ga.OAUTH_ENV)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ALL_ENV:
        monkeypatch.delenv(key, raising=False)
    # credentials() and service() are lru_cached; a stale entry would leak the
    # previous test's auth mode into this one.
    ga.credentials.cache_clear()
    ga.service.cache_clear()
    yield
    ga.credentials.cache_clear()
    ga.service.cache_clear()


def test_no_credentials_is_none() -> None:
    assert ga.auth_mode() is None


def test_partial_oauth_is_not_enough(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    # Refresh token missing: usable credentials cannot be built.
    assert ga.auth_mode() is None


def test_full_oauth_detected(monkeypatch) -> None:
    for k in ga.OAUTH_ENV:
        monkeypatch.setenv(k, "x")
    assert ga.auth_mode() == "oauth"


def test_service_account_wins_over_oauth(monkeypatch) -> None:
    for k in ga.OAUTH_ENV:
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv(ga.SA_ENV, '{"type": "service_account"}')
    # Preferred because it needs no consent screen and no verification.
    assert ga.auth_mode() == "service_account"


def test_missing_credentials_message_names_both_routes(monkeypatch) -> None:
    with pytest.raises(ga.MissingCredentials) as exc:
        ga.credentials()
    msg = str(exc.value)
    assert ga.SA_ENV in msg
    assert "GOOGLE_REFRESH_TOKEN" in msg


def test_service_account_json_inline(monkeypatch) -> None:
    payload = {"type": "service_account", "client_email": "sa@p.iam.gserviceaccount.com"}
    monkeypatch.setenv(ga.SA_ENV, json.dumps(payload))
    assert ga._service_account_info() == payload


def test_service_account_json_from_a_path(monkeypatch, tmp_path) -> None:
    payload = {"type": "service_account", "client_email": "sa@p.iam.gserviceaccount.com"}
    p = tmp_path / "sa.json"
    p.write_text(json.dumps(payload))
    # A GitHub secret holds the blob; a local .env more naturally holds a path.
    monkeypatch.setenv(ga.SA_ENV, str(p))
    assert ga._service_account_info() == payload


def test_service_account_path_that_does_not_exist(monkeypatch) -> None:
    monkeypatch.setenv(ga.SA_ENV, "/nope/sa.json")
    with pytest.raises(ga.MissingCredentials, match="neither JSON nor an existing file"):
        ga._service_account_info()


def test_service_account_scopes_exclude_gmail() -> None:
    assert ga.GMAIL_SCOPE not in ga.SERVICE_ACCOUNT_SCOPES
    assert ga.GMAIL_SCOPE in ga.SCOPES


def test_gmail_with_a_service_account_points_at_imap(monkeypatch) -> None:
    monkeypatch.setenv(ga.SA_ENV, '{"type": "service_account"}')
    with pytest.raises(ga.MissingCredentials, match="imap"):
        ga.service("gmail", "v1")


def test_sheets_with_a_service_account_builds(monkeypatch) -> None:
    built: dict = {}

    class FakeSACreds:
        @staticmethod
        def from_service_account_info(info, scopes):
            built["info"] = info
            built["scopes"] = scopes
            return "sa-creds"

    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials", FakeSACreds, raising=True
    )
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda api, version, credentials, cache_discovery: f"{api}:{version}:{credentials}",
        raising=True,
    )
    monkeypatch.setenv(ga.SA_ENV, '{"type": "service_account"}')

    assert ga.service("sheets", "v4") == "sheets:v4:sa-creds"
    assert built["scopes"] == ga.SERVICE_ACCOUNT_SCOPES
